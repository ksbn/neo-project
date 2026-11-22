"""PostgreSQL adapters for the REST API: :class:`ObjectRepository` (catalog queries) and
:class:`WatchlistRepository` (the watchlist, with idempotent writes).

Like ``repository.py``, every ``psycopg.Error`` is translated at this boundary: constraint
violations become the domain errors the API maps to ``409`` / ``422``, anything else a
:class:`domain.errors.StorageError`, so no driver exception reaches a client.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any, Final, LiteralString

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from planetary_defense.domain.catalog import (
    IdempotencyClaim,
    ObjectPage,
    ObjectQuery,
    ObjectRow,
    Priority,
    Summary,
    WatchlistEntry,
    WatchlistPage,
    WatchRequest,
    WatchResult,
)
from planetary_defense.domain.errors import AlreadyWatched, IdempotencyKeyReused, StorageError, UnknownObject
from planetary_defense.domain.models import AnomalyAlert, ApproachSummary, Severity
from planetary_defense.domain.units import LUNAR_DISTANCE_KM
from planetary_defense.infrastructure.repository import _storage_error

__all__ = ["ObjectRepository", "WatchlistRepository"]

# The latest pass of every object, with the worst alert raised for that pass.
_BASE: Final = """
    SELECT a.neo_reference_id, a.name, a.is_potentially_hazardous, a.is_sentry_object, a.absolute_magnitude_h,
           a.estimated_diameter_min_km, a.estimated_diameter_max_km, a.close_approach_date, a.orbiting_body,
           a.relative_velocity_km_s, a.miss_distance_km, a.nasa_jpl_url,
           (SELECT MAX(x.severity) FROM anomaly_alerts x
            WHERE x.neo_reference_id = a.neo_reference_id AND x.close_approach_date = a.close_approach_date)
               AS alert_level,
           EXISTS (SELECT 1 FROM watchlist w WHERE w.neo_reference_id = a.neo_reference_id) AS watched
    FROM asteroids a
"""

# Every order ends with the primary key, so pages never overlap or skip rows.
_ORDER: Final[dict[str, LiteralString]] = {
    "-approach_date": "close_approach_date DESC NULLS LAST",
    "approach_date": "close_approach_date ASC NULLS LAST",
    "distance": "miss_distance_km ASC NULLS LAST",
    "-distance": "miss_distance_km DESC NULLS LAST",
    "-velocity": "relative_velocity_km_s DESC NULLS LAST",
    "velocity": "relative_velocity_km_s ASC NULLS LAST",
    "-diameter": "estimated_diameter_max_km DESC NULLS LAST",
    "name": "lower(name) ASC",
}


def _num(value: Any) -> float | None:
    return float(value) if value is not None else None


def _object_row(r: Sequence[Any]) -> ObjectRow:
    (ref, name, pha, sentry, h, dmin, dmax, day, body, velocity, miss, _url, level, watched) = r
    approach = (ApproachSummary(day, body or "Earth", float(velocity), float(miss))
                if day is not None and velocity is not None and miss is not None else None)
    return ObjectRow(
        neo_reference_id=ref, name=name, is_potentially_hazardous=bool(pha), is_sentry_object=bool(sentry),
        absolute_magnitude_h=_num(h), estimated_diameter_min_km=_num(dmin), estimated_diameter_max_km=_num(dmax),
        latest_approach=approach, alert_level=Severity(level) if level is not None else None,
        watched=bool(watched),
    )


def _filters(query: ObjectQuery) -> tuple[sql.Composable, list[Any]]:
    """The WHERE clause (fixed SQL fragments only) and its parameters."""
    conditions: list[tuple[LiteralString, Any]] = []
    if query.q:
        conditions.append(("strpos(lower(name), lower(%s)) > 0", query.q))  # a substring: no LIKE wildcards
    if query.hazardous is not None:
        conditions.append(("is_potentially_hazardous = %s", query.hazardous))
    if query.max_distance_ld is not None:
        conditions.append(("miss_distance_km <= %s", query.max_distance_ld * LUNAR_DISTANCE_KM))
    if query.min_velocity_km_s is not None:
        conditions.append(("relative_velocity_km_s >= %s", query.min_velocity_km_s))
    if query.min_severity is not None:
        conditions.append(("alert_level >= %s", int(query.min_severity)))
    if query.watched is not None:
        conditions.append(("watched = %s", query.watched))
    if query.approach_from is not None:
        conditions.append(("close_approach_date >= %s", query.approach_from))
    if query.approach_to is not None:
        conditions.append(("close_approach_date <= %s", query.approach_to))
    if not conditions:
        return sql.SQL(""), []
    where = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(sql.SQL(c) for c, _ in conditions)
    return where, [v for _, v in conditions]


class ObjectRepository:
    """Catalog queries over ``asteroids`` and ``anomaly_alerts``. Satisfies :class:`application.ports.ObjectReader`."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def search(self, query: ObjectQuery) -> ObjectPage:
        where, params = _filters(query)
        base = sql.SQL(_BASE)
        count = sql.SQL("SELECT COUNT(*) FROM ({base}) o{where}").format(base=base, where=where)
        page = sql.SQL("SELECT * FROM ({base}) o{where} ORDER BY {order}, neo_reference_id LIMIT %s OFFSET %s").format(
            base=base, where=where, order=sql.SQL(_ORDER[query.sort]))
        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
                cur.execute(count, params)
                total_row = cur.fetchone()
                cur.execute(page, [*params, query.limit, query.offset])
                rows = cur.fetchall()
        except psycopg.Error as exc:
            raise _storage_error("Searching objects", exc) from exc
        return ObjectPage(rows=[_object_row(r) for r in rows], total=int(total_row[0]) if total_row else 0)

    def find(self, neo_reference_id: str) -> tuple[ObjectRow, str | None] | None:
        """The object and its NASA JPL URL, or None when it is not stored."""
        query = sql.SQL("SELECT * FROM ({base}) o WHERE neo_reference_id = %s").format(base=sql.SQL(_BASE))
        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
                cur.execute(query, (neo_reference_id,))
                row = cur.fetchone()
        except psycopg.Error as exc:
            raise _storage_error("Reading the object", exc) from exc
        return (_object_row(row), row[11]) if row is not None else None

    def alerts_for(self, neo_reference_id: str) -> list[AnomalyAlert]:
        query = """
            SELECT a.rule, a.severity, a.neo_reference_id, s.name, a.close_approach_date, a.message, a.value,
                   a.threshold
            FROM anomaly_alerts a JOIN asteroids s USING (neo_reference_id)
            WHERE a.neo_reference_id = %s
            ORDER BY a.severity DESC, a.close_approach_date DESC, a.rule
        """
        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
                cur.execute(query, (neo_reference_id,))
                rows = cur.fetchall()
        except psycopg.Error as exc:
            raise _storage_error("Reading the object's alerts", exc) from exc
        return [AnomalyAlert(rule, Severity(sev), ref, name, day, msg, value, threshold)
                for rule, sev, ref, name, day, msg, value, threshold in rows]


    def summary(self, today: date, days: int = 14) -> Summary:
        """KPI aggregates in one transaction (ADR 0001)."""
        since = today - timedelta(days=days - 1)
        totals = f"""
            SELECT COUNT(*), COUNT(*) FILTER (WHERE is_potentially_hazardous), COUNT(*) FILTER (WHERE watched),
                   MAX(close_approach_date),
                   COUNT(*) FILTER (WHERE alert_level = 1), COUNT(*) FILTER (WHERE alert_level = 2),
                   COUNT(*) FILTER (WHERE alert_level = 3), COUNT(*) FILTER (WHERE alert_level = 4)
            FROM ({_BASE}) o
        """
        closest = f"""
            SELECT * FROM ({_BASE}) o WHERE miss_distance_km IS NOT NULL
            ORDER BY miss_distance_km, neo_reference_id LIMIT 1
        """
        per_day = """
            SELECT d::date, COUNT(c.neo_reference_id)
            FROM generate_series(%s::date, %s::date, INTERVAL '1 day') AS d
            LEFT JOIN close_approaches c ON c.close_approach_date = d::date
            GROUP BY d ORDER BY d
        """
        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
                cur.execute(totals)
                t = cur.fetchone()
                cur.execute(closest)
                c = cur.fetchone()
                cur.execute("SELECT MAX(finished_at) FROM ingestion_runs WHERE status = 'success'")
                sync = cur.fetchone()
                cur.execute(per_day, (since, today))
                days_rows = cur.fetchall()
        except psycopg.Error as exc:
            raise _storage_error("Summarising objects", exc) from exc
        assert t is not None  # an aggregate always returns one row
        total, hazardous, watched, latest, *levels = t
        closest_pass = None
        if c is not None:
            row = _object_row(c)
            if row.latest_approach is not None:
                closest_pass = (row.neo_reference_id, row.name, row.latest_approach)
        return Summary(
            total_objects=int(total), hazardous_objects=int(hazardous), watched_objects=int(watched),
            alert_counts={s: int(n) for s, n in zip(Severity, levels, strict=True)},
            closest=closest_pass, latest_approach_date=latest, last_sync_at=sync[0] if sync else None,
            approaches_per_day=[(d, int(n)) for d, n in days_rows],
        )


# --------------------------------------------------------------------------- #
# Watchlist
# --------------------------------------------------------------------------- #

IDEMPOTENCY_TTL: Final = timedelta(hours=24)

_ENTRY_COLUMNS: Final = "w.id::text, w.neo_reference_id, a.name, w.priority, w.alert_threshold_ld, w.note, w.tags, " \
                        "w.created_at"

_INSERT: Final = f"""
    WITH w AS (
        INSERT INTO watchlist (neo_reference_id, priority, alert_threshold_ld, note, tags)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING *
    )
    SELECT {_ENTRY_COLUMNS} FROM w JOIN asteroids a USING (neo_reference_id)
"""


_FIND_KEY: Final = """
    SELECT request_hash, response_body FROM idempotency_keys
    WHERE scope = %s AND key = %s AND expires_at > NOW()
"""
_STORE_KEY: Final = """
    INSERT INTO idempotency_keys (scope, key, request_hash, status_code, response_body, expires_at)
    VALUES (%s, %s, %s, 201, %s, NOW() + %s)
"""


def _entry(r: Sequence[Any]) -> WatchlistEntry:
    wid, ref, name, priority, threshold, note, tags, created = r
    return WatchlistEntry(wid, ref, name, Priority(priority), threshold, note, tuple(tags), created)


def _entry_json(e: WatchlistEntry) -> dict[str, Any]:
    return {"id": e.id, "neo_reference_id": e.neo_reference_id, "name": e.name, "priority": e.priority.value,
            "alert_threshold_ld": e.alert_threshold_ld, "note": e.note, "tags": list(e.tags),
            "created_at": e.created_at.isoformat()}


def _entry_from_json(d: dict[str, Any]) -> WatchlistEntry:
    return WatchlistEntry(d["id"], d["neo_reference_id"], d["name"], Priority(d["priority"]),
                          d["alert_threshold_ld"], d["note"], tuple(d["tags"]),
                          datetime.fromisoformat(d["created_at"]))


class WatchlistRepository:
    """The ``watchlist`` and ``idempotency_keys`` tables. Satisfies :class:`application.ports.WatchlistStore`.

    :meth:`add` is idempotent per :class:`IdempotencyClaim`. In one transaction it
      1. takes a transaction-scoped advisory lock on the key, so concurrent retries of the
         same request run one after the other instead of racing;
      2. drops expired keys;
      3. replays the stored response if the key was used with the same request hash, or
         raises :class:`IdempotencyKeyReused` if it was used with a different one;
      4. otherwise inserts the entry and stores the response under the key.
    Only successful writes are stored: a request that failed changed nothing, so retrying
    it with the same key simply runs it again.
    """

    def __init__(self, conn: psycopg.Connection, *, ttl: timedelta = IDEMPOTENCY_TTL) -> None:
        self._conn = conn
        self._ttl = ttl

    def add(self, request: WatchRequest, claim: IdempotencyClaim | None = None) -> WatchResult:
        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
                if claim is not None:
                    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                                (f"{claim.scope}\x1f{claim.key}",))
                    cur.execute("DELETE FROM idempotency_keys WHERE expires_at <= NOW()")
                    cur.execute(_FIND_KEY, (claim.scope, claim.key))
                    stored = cur.fetchone()
                    if stored is not None:
                        if stored[0] != claim.request_hash:
                            raise IdempotencyKeyReused(
                                "This Idempotency-Key was already used with a different request body.")
                        return WatchResult(_entry_from_json(stored[1]), replayed=True)
                cur.execute(_INSERT, (request.neo_reference_id, request.priority.value, request.alert_threshold_ld,
                                      request.note, list(request.tags)))
                row = cur.fetchone()
                if row is None:  # pragma: no cover - the insert returns its row or raises
                    raise StorageError("The watchlist insert returned no row")
                entry = _entry(row)
                if claim is not None:
                    cur.execute(
                        _STORE_KEY,
                        (claim.scope, claim.key, claim.request_hash, Jsonb(_entry_json(entry)), self._ttl),
                    )
        except psycopg.errors.UniqueViolation as exc:
            raise AlreadyWatched(f"Object {request.neo_reference_id} is already on the watchlist.") from exc
        except psycopg.errors.ForeignKeyViolation as exc:
            raise UnknownObject(f"No near-Earth object with ID {request.neo_reference_id} is stored.") from exc
        except psycopg.Error as exc:
            raise _storage_error("Adding to the watchlist", exc) from exc
        return WatchResult(entry)

    def watch_of(self, neo_reference_id: str) -> WatchlistEntry | None:
        query = f"SELECT {_ENTRY_COLUMNS} FROM watchlist w JOIN asteroids a USING (neo_reference_id) " \
                "WHERE w.neo_reference_id = %s"
        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
                cur.execute(query, (neo_reference_id,))
                row = cur.fetchone()
        except psycopg.Error as exc:
            raise _storage_error("Reading the watchlist", exc) from exc
        return _entry(row) if row is not None else None

    def entries(self, limit: int, offset: int) -> WatchlistPage:
        query = f"SELECT {_ENTRY_COLUMNS} FROM watchlist w JOIN asteroids a USING (neo_reference_id) " \
                "ORDER BY w.created_at DESC, w.id LIMIT %s OFFSET %s"
        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM watchlist")
                total = cur.fetchone()
                cur.execute(query, (limit, offset))
                rows = cur.fetchall()
        except psycopg.Error as exc:
            raise _storage_error("Listing the watchlist", exc) from exc
        return WatchlistPage([_entry(r) for r in rows], int(total[0]) if total else 0)
