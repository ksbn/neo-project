"""PostgreSQL repositories: :class:`AsteroidRepository` and :class:`IngestionRunRepository`.

They work on a connection opened by :func:`infrastructure.db.connect`; this
module never reads the environment. Every ``psycopg.Error`` is translated to
:class:`domain.errors.StorageError` at this boundary.

Tables:

* ``asteroids``: one row per asteroid with its newest close approach (the
  current-state view). Which approach is newest is decided by
  :func:`domain.models.latest`; the ``WHERE`` guard in :data:`UPSERT_SQL` enforces the
  same rule at storage level, so an older or concurrent run can never
  overwrite newer data.
* ``close_approaches``: every approach ever seen (history), one row per
  asteroid, date and body.
* ``ingestion_runs``: one row per pipeline run (:class:`IngestionRunRepository`).

Every read runs in its own explicit transaction, so a read never leaves an
implicit transaction open that a later write would silently join.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from datetime import date
from typing import Any, Final

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from planetary_defense.domain.errors import StorageError
from planetary_defense.domain.models import (
    AnomalyAlert,
    ApproachSummary,
    AsteroidSummary,
    CachedOrbit,
    IngestionRun,
    Observation,
    OrbitalElements,
    OrbitData,
    Overview,
    PipelineStatus,
    RunStatus,
    Severity,
    UpsertResult,
    latest,
)
from planetary_defense.domain.units import km_to_ld

__all__ = ["AlertRepository", "AsteroidRepository", "IngestionRunRepository", "OrbitRepository", "UpsertResult"]

logger = logging.getLogger(__name__)

TABLE: Final = "asteroids"
KEY_COLUMN: Final = "neo_reference_id"
# Columns written by the pipeline (created_at / updated_at are maintained by SQL).
COLUMNS: Final = (
    "neo_reference_id",
    "name",
    "nasa_jpl_url",
    "absolute_magnitude_h",
    "estimated_diameter_min_km",
    "estimated_diameter_max_km",
    "is_potentially_hazardous",
    "is_sentry_object",
    "close_approach_date",
    "relative_velocity_km_s",
    "miss_distance_km",
    "orbiting_body",
)
UPDATE_COLUMNS: Final = tuple(c for c in COLUMNS if c != KEY_COLUMN)
SUMMARY_COLUMNS: Final = (
    "neo_reference_id",
    "name",
    "estimated_diameter_min_km",
    "estimated_diameter_max_km",
    "is_potentially_hazardous",
    "close_approach_date",
    "miss_distance_km",
    "relative_velocity_km_s",
)


def _storage_error(action: str, exc: psycopg.Error) -> StorageError:
    code = f" [SQLSTATE {exc.sqlstate}]" if exc.sqlstate else ""
    return StorageError(f"{action} failed{code}: {exc}")


def _row(obs: Observation) -> dict[str, Any]:
    """Flatten an observation into the ``asteroids`` table's columns."""
    approach = obs.approach
    return {
        **obs.asteroid.model_dump(),
        "close_approach_date": approach.close_approach_date if approach else None,
        "relative_velocity_km_s": approach.relative_velocity_km_s if approach else None,
        "miss_distance_km": approach.miss_distance_km if approach else None,
        "orbiting_body": approach.orbiting_body if approach else None,
    }


def _build_upsert_sql() -> sql.Composed:
    """Build the idempotent upsert statement.

    An existing row is only updated when
      1. the incoming close approach is not older than the stored one, so
         re-loading an old date range never overwrites newer data, and
      2. at least one value actually differs, so re-runs don't touch rows
         (``updated_at`` then reflects real changes only).

    ``RETURNING (xmax = 0)`` is true for inserted rows and false for updated
    ones; rows skipped by the WHERE clause return nothing.
    """
    table = sql.Identifier(TABLE)
    cols = sql.SQL(", ").join(map(sql.Identifier, COLUMNS))
    values = sql.SQL(", ").join(map(sql.Placeholder, COLUMNS))
    assignments = sql.SQL(", ").join(
        sql.SQL("{c} = EXCLUDED.{c}").format(c=sql.Identifier(c)) for c in UPDATE_COLUMNS
    )
    current_row = sql.SQL(", ").join(sql.SQL("{t}.{c}").format(t=table, c=sql.Identifier(c)) for c in UPDATE_COLUMNS)
    incoming_row = sql.SQL(", ").join(sql.SQL("EXCLUDED.{c}").format(c=sql.Identifier(c)) for c in UPDATE_COLUMNS)

    return sql.SQL(
        """
        INSERT INTO {table} ({cols})
        VALUES ({values})
        ON CONFLICT ({key}) DO UPDATE
        SET {assignments}, updated_at = NOW()
        WHERE ({table}.close_approach_date IS NULL
               OR EXCLUDED.close_approach_date >= {table}.close_approach_date)
          AND ({current_row}) IS DISTINCT FROM ({incoming_row})
        RETURNING (xmax = 0) AS inserted
        """
    ).format(
        table=table,
        cols=cols,
        values=values,
        key=sql.Identifier(KEY_COLUMN),
        assignments=assignments,
        current_row=current_row,
        incoming_row=incoming_row,
    )


UPSERT_SQL: Final = _build_upsert_sql()

# History rows: insert new passes, update a pass only if NASA revised its numbers.
APPROACH_UPSERT_SQL: Final = """
    INSERT INTO close_approaches
        (neo_reference_id, close_approach_date, orbiting_body, relative_velocity_km_s, miss_distance_km)
    VALUES
        (%(neo_reference_id)s, %(close_approach_date)s, %(orbiting_body)s,
         %(relative_velocity_km_s)s, %(miss_distance_km)s)
    ON CONFLICT (neo_reference_id, close_approach_date, orbiting_body) DO UPDATE
    SET relative_velocity_km_s = EXCLUDED.relative_velocity_km_s,
        miss_distance_km = EXCLUDED.miss_distance_km,
        updated_at = NOW()
    WHERE (close_approaches.relative_velocity_km_s, close_approaches.miss_distance_km)
          IS DISTINCT FROM (EXCLUDED.relative_velocity_km_s, EXCLUDED.miss_distance_km)
    RETURNING (xmax = 0) AS inserted
"""


def _approach_rows(observations: Iterable[Observation]) -> list[dict[str, Any]]:
    """History rows, one per (asteroid, date, body); the last observation of a pass wins."""
    rows: dict[tuple[str, Any, str], dict[str, Any]] = {}
    for obs in observations:
        if obs.approach is None:
            continue
        a = obs.approach
        rows[(obs.neo_reference_id, a.close_approach_date, a.orbiting_body)] = {
            "neo_reference_id": obs.neo_reference_id,
            "close_approach_date": a.close_approach_date,
            "orbiting_body": a.orbiting_body,
            "relative_velocity_km_s": a.relative_velocity_km_s,
            "miss_distance_km": a.miss_distance_km,
        }
    return list(rows.values())


def _count_returning(cur: psycopg.Cursor[Any]) -> tuple[int, int]:
    """(true, false) counts of a ``RETURNING <bool>`` over every executemany result set."""
    true = false = 0
    while True:
        row = cur.fetchone()
        if row is not None:
            if row[0]:
                true += 1
            else:
                false += 1
        if not cur.nextset():
            break
    return true, false


class AsteroidRepository:
    """The ``asteroids`` and ``close_approaches`` tables.

    Satisfies :class:`application.ports.AsteroidStore` and :class:`application.ports.AsteroidReader`.
    """

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    # ----------------------------------------------------------------- write

    def save(self, observations: Sequence[Observation]) -> UpsertResult:
        """Store the newest observation per asteroid and every approach, atomically and idempotently.

        ``inserted`` / ``updated`` / ``unchanged`` count asteroids;
        ``new_approaches`` counts history rows that didn't exist before. All
        rows are written in one transaction: either everything is applied or nothing is.

        Raises:
            StorageError: A statement failed; the transaction was rolled back.
        """
        rows = [_row(obs) for obs in latest(observations)]
        if not rows:
            return UpsertResult()
        history = _approach_rows(observations)

        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
                # Asteroids first: close_approaches references them.
                cur.executemany(UPSERT_SQL, rows, returning=True)
                inserted, updated = _count_returning(cur)
                new_approaches = 0
                if history:
                    cur.executemany(APPROACH_UPSERT_SQL, history, returning=True)
                    new_approaches, _ = _count_returning(cur)
        except psycopg.Error as exc:
            raise _storage_error("Upsert", exc) from exc

        return UpsertResult(
            inserted=inserted,
            updated=updated,
            unchanged=len(rows) - inserted - updated,
            new_approaches=new_approaches,
        )

    def count(self) -> int:
        """Total number of asteroids stored."""
        return self.overview().total

    # ------------------------------------------------------------------ read

    def overview(self) -> Overview:
        """Totals over the whole table (not limited like :meth:`list_recent`)."""
        query = """
            SELECT COUNT(*), COUNT(*) FILTER (WHERE is_potentially_hazardous), MAX(close_approach_date),
                   h.passes, h.closest_km, h.fastest_km_s
            FROM asteroids,
                 (SELECT COUNT(*) AS passes, MIN(miss_distance_km) AS closest_km,
                         MAX(relative_velocity_km_s) AS fastest_km_s
                  FROM close_approaches) AS h
            GROUP BY h.passes, h.closest_km, h.fastest_km_s
        """
        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
                cur.execute(query)
                row = cur.fetchone()
        except psycopg.Error as exc:
            raise _storage_error("Overview", exc) from exc
        if row is None:  # an aggregate always returns one row
            return Overview(total=0, hazardous=0, latest_approach=None)
        total, hazardous, latest, passes, closest_km, fastest = row
        return Overview(
            total=int(total), hazardous=int(hazardous), latest_approach=latest, approaches=int(passes),
            closest_miss_ld=km_to_ld(float(closest_km)) if closest_km is not None else None,
            fastest_km_s=float(fastest) if fastest is not None else None,
        )

    def list_recent(self, limit: int = 5_000) -> list[AsteroidSummary]:
        """The newest ``limit`` asteroids by close approach."""
        if limit < 1:
            raise ValueError("limit must be at least 1")
        query = sql.SQL(
            "SELECT {cols} FROM {table} ORDER BY close_approach_date DESC NULLS LAST, {key} LIMIT %s"
        ).format(
            cols=sql.SQL(", ").join(map(sql.Identifier, SUMMARY_COLUMNS)),
            table=sql.Identifier(TABLE),
            key=sql.Identifier(KEY_COLUMN),
        )
        try:
            with self._conn.transaction(), self._conn.cursor(row_factory=dict_row) as cur:
                cur.execute(query, (limit,))
                rows = cur.fetchall()
        except psycopg.Error as exc:
            raise _storage_error("Listing asteroids", exc) from exc
        return [_summary(r) for r in rows]

    def daily_approaches(self, since: date) -> list[tuple[date, int]]:
        """Recorded close approaches per day from ``since`` on (days without any are omitted)."""
        query = """
            SELECT close_approach_date, COUNT(*) FROM close_approaches
            WHERE close_approach_date >= %s GROUP BY 1 ORDER BY 1
        """
        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
                cur.execute(query, (since,))
                rows = cur.fetchall()
        except psycopg.Error as exc:
            raise _storage_error("Daily approaches", exc) from exc
        return [(day, int(n)) for day, n in rows]

    def approach_history(self, neo_reference_id: str, limit: int = 100) -> list[ApproachSummary]:
        """Recorded approaches of one asteroid, newest first."""
        if limit < 1:
            raise ValueError("limit must be at least 1")
        query = """
            SELECT close_approach_date, orbiting_body, relative_velocity_km_s, miss_distance_km
            FROM close_approaches
            WHERE neo_reference_id = %s
            ORDER BY close_approach_date DESC, orbiting_body
            LIMIT %s
        """
        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
                cur.execute(query, (neo_reference_id, limit))
                rows = cur.fetchall()
        except psycopg.Error as exc:
            raise _storage_error("Approach history", exc) from exc
        return [ApproachSummary(day, body, float(velocity), float(distance)) for day, body, velocity, distance in rows]


def _summary(row: dict[str, Any]) -> AsteroidSummary:
    def num(value: Any) -> float | None:
        return float(value) if value is not None else None

    return AsteroidSummary(
        neo_reference_id=row["neo_reference_id"],
        name=row["name"],
        estimated_diameter_min_km=num(row["estimated_diameter_min_km"]),
        estimated_diameter_max_km=num(row["estimated_diameter_max_km"]),
        is_potentially_hazardous=bool(row["is_potentially_hazardous"]),
        close_approach_date=row["close_approach_date"],
        miss_distance_km=num(row["miss_distance_km"]),
        relative_velocity_km_s=num(row["relative_velocity_km_s"]),
    )


_RUN_COLUMNS: Final = (
    "started_at", "finished_at", "start_date", "end_date", "status", "windows_total", "windows_done",
    "fetched", "inserted", "updated", "unchanged", "invalid", "missing_dates", "new_approaches",
    "error_category", "error_message",
)
_MAX_ERROR_MESSAGE: Final = 1_000


class IngestionRunRepository:
    """The ``ingestion_runs`` table. Satisfies :class:`application.ports.RunLog` and ``RunReader``."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def record(self, run: IngestionRun) -> None:
        values = {name: getattr(run, name) for name in _RUN_COLUMNS}
        values["status"] = run.status.value
        if run.error_message is not None:
            values["error_message"] = run.error_message[:_MAX_ERROR_MESSAGE]
        query = sql.SQL("INSERT INTO ingestion_runs ({cols}) VALUES ({vals})").format(
            cols=sql.SQL(", ").join(map(sql.Identifier, _RUN_COLUMNS)),
            vals=sql.SQL(", ").join(map(sql.Placeholder, _RUN_COLUMNS)),
        )
        try:
            with self._conn.transaction():
                self._conn.execute(query, values)
        except psycopg.Error as exc:
            raise _storage_error("Recording the run", exc) from exc

    def pipeline_status(self) -> PipelineStatus:
        latest_run = sql.SQL("SELECT {cols} FROM ingestion_runs ORDER BY finished_at DESC, id DESC LIMIT 1").format(
            cols=sql.SQL(", ").join(map(sql.Identifier, _RUN_COLUMNS))
        )
        try:
            with self._conn.transaction(), self._conn.cursor(row_factory=dict_row) as cur:
                cur.execute(latest_run)
                row = cur.fetchone()
                cur.execute("SELECT MAX(finished_at) AS at FROM ingestion_runs WHERE status = 'success'")
                success = cur.fetchone()
        except psycopg.Error as exc:
            raise _storage_error("Reading pipeline status", exc) from exc
        last_run = IngestionRun(**{**row, "status": RunStatus(row["status"])}) if row else None
        return PipelineStatus(last_run=last_run, last_success_at=success["at"] if success else None)


# --------------------------------------------------------------------------- #
# Orbit cache (orbital_elements)
# --------------------------------------------------------------------------- #

_ORBIT_COLUMNS: Final = (
    "neo_reference_id", "semi_major_axis_au", "eccentricity", "inclination_deg", "ascending_node_deg",
    "perihelion_argument_deg", "mean_anomaly_deg", "epoch_jd", "orbit_id", "orbit_class", "moid_au", "fetched_at",
)


class OrbitRepository:
    """The ``orbital_elements`` table. Satisfies :class:`application.ports.OrbitCache`."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def get_orbit(self, neo_reference_id: str) -> CachedOrbit | None:
        query = sql.SQL("SELECT {cols} FROM orbital_elements WHERE neo_reference_id = %s").format(
            cols=sql.SQL(", ").join(map(sql.Identifier, _ORBIT_COLUMNS))
        )
        try:
            with self._conn.transaction(), self._conn.cursor(row_factory=dict_row) as cur:
                cur.execute(query, (neo_reference_id,))
                row = cur.fetchone()
        except psycopg.Error as exc:
            raise _storage_error("Reading the orbit cache", exc) from exc
        if row is None:
            return None
        elements = OrbitalElements(
            semi_major_axis_au=row["semi_major_axis_au"], eccentricity=row["eccentricity"],
            inclination_deg=row["inclination_deg"], ascending_node_deg=row["ascending_node_deg"],
            perihelion_argument_deg=row["perihelion_argument_deg"], mean_anomaly_deg=row["mean_anomaly_deg"],
            epoch_jd=row["epoch_jd"],
        )
        orbit = OrbitData(
            neo_reference_id=row["neo_reference_id"], elements=elements, orbit_id=row["orbit_id"],
            orbit_class=row["orbit_class"], moid_au=row["moid_au"],
        )
        return CachedOrbit(orbit=orbit, fetched_at=row["fetched_at"])

    def put_orbit(self, orbit: CachedOrbit) -> None:
        data, el = orbit.orbit, orbit.orbit.elements
        values = {
            "neo_reference_id": data.neo_reference_id, "semi_major_axis_au": el.semi_major_axis_au,
            "eccentricity": el.eccentricity, "inclination_deg": el.inclination_deg,
            "ascending_node_deg": el.ascending_node_deg, "perihelion_argument_deg": el.perihelion_argument_deg,
            "mean_anomaly_deg": el.mean_anomaly_deg, "epoch_jd": el.epoch_jd, "orbit_id": data.orbit_id,
            "orbit_class": data.orbit_class, "moid_au": data.moid_au, "fetched_at": orbit.fetched_at,
        }
        query = sql.SQL(
            "INSERT INTO orbital_elements ({cols}) VALUES ({vals}) "
            "ON CONFLICT (neo_reference_id) DO UPDATE SET {updates}"
        ).format(
            cols=sql.SQL(", ").join(map(sql.Identifier, _ORBIT_COLUMNS)),
            vals=sql.SQL(", ").join(map(sql.Placeholder, _ORBIT_COLUMNS)),
            updates=sql.SQL(", ").join(
                sql.SQL("{c} = EXCLUDED.{c}").format(c=sql.Identifier(c)) for c in _ORBIT_COLUMNS[1:]
            ),
        )
        try:
            with self._conn.transaction():
                self._conn.execute(query, values)
        except psycopg.Error as exc:
            raise _storage_error("Caching the orbit", exc) from exc


# --------------------------------------------------------------------------- #
# Alerts (anomaly_alerts)
# --------------------------------------------------------------------------- #

_ALERT_UPSERT_SQL: Final = """
    INSERT INTO anomaly_alerts
        (neo_reference_id, close_approach_date, rule, severity, message, value, threshold)
    VALUES
        (%(neo_reference_id)s, %(close_approach_date)s, %(rule)s, %(severity)s, %(message)s, %(value)s, %(threshold)s)
    ON CONFLICT (neo_reference_id, close_approach_date, rule) DO UPDATE
    SET severity = EXCLUDED.severity, message = EXCLUDED.message, value = EXCLUDED.value,
        threshold = EXCLUDED.threshold, updated_at = NOW()
    WHERE (anomaly_alerts.severity, anomaly_alerts.message, anomaly_alerts.value, anomaly_alerts.threshold)
          IS DISTINCT FROM (EXCLUDED.severity, EXCLUDED.message, EXCLUDED.value, EXCLUDED.threshold)
"""

# Alerts of the given passes that are not in the current set: rules that no longer fire.
_ALERT_PRUNE_SQL: Final = """
    DELETE FROM anomaly_alerts a
    USING unnest(%s::text[], %s::date[]) AS p(neo, day)
    WHERE a.neo_reference_id = p.neo AND a.close_approach_date = p.day
      AND (a.neo_reference_id, a.close_approach_date, a.rule) NOT IN (
          SELECT * FROM unnest(%s::text[], %s::date[], %s::text[]))
"""


class AlertRepository:
    """The ``anomaly_alerts`` table. Satisfies :class:`application.ports.AlertStore` and ``AlertReader``."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def replace_alerts(self, passes: Sequence[tuple[str, date]], alerts: Sequence[AnomalyAlert]) -> None:
        """Make ``alerts`` the complete set for ``passes``, atomically. Alerts without a date are skipped.

        Unchanged alerts are left untouched, so ``raised_at`` records when an alert first appeared.
        """
        passes = list(dict.fromkeys(passes))
        current = {
            (a.neo_reference_id, a.close_approach_date, a.rule): a for a in alerts if a.close_approach_date is not None
        }
        rows = [
            {
                "neo_reference_id": a.neo_reference_id, "close_approach_date": a.close_approach_date, "rule": a.rule,
                "severity": int(a.severity), "message": a.message, "value": a.value, "threshold": a.threshold,
            }
            for a in current.values()
        ]
        keep = list(current)
        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
                if rows:
                    cur.executemany(_ALERT_UPSERT_SQL, rows)
                if passes:
                    cur.execute(
                        _ALERT_PRUNE_SQL,
                        ([p[0] for p in passes], [p[1] for p in passes],
                         [k[0] for k in keep], [k[1] for k in keep], [k[2] for k in keep]),
                    )
        except psycopg.Error as exc:
            raise _storage_error("Saving alerts", exc) from exc

    def recent_alerts(self, min_severity: Severity = Severity.INFO, limit: int = 200) -> list[AnomalyAlert]:
        """Alerts at or above ``min_severity``: most urgent first, then the newest passes."""
        query = """
            SELECT a.rule, a.severity, a.neo_reference_id, s.name, a.close_approach_date, a.message, a.value,
                   a.threshold
            FROM anomaly_alerts a JOIN asteroids s USING (neo_reference_id)
            WHERE a.severity >= %s
            ORDER BY a.severity DESC, a.close_approach_date DESC, a.rule, a.neo_reference_id
            LIMIT %s
        """
        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
                cur.execute(query, (int(min_severity), limit))
                rows = cur.fetchall()
        except psycopg.Error as exc:
            raise _storage_error("Listing alerts", exc) from exc
        return [
            AnomalyAlert(rule, Severity(severity), ref, name, day, message, value, threshold)
            for rule, severity, ref, name, day, message, value, threshold in rows
        ]
