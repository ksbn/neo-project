"""PostgreSQL adapter: connections and :class:`AsteroidRepository`.

Connection settings arrive as a :class:`config.DbConfig`; this module never
reads the environment. Every ``psycopg.Error`` is translated to
:class:`errors.StorageError` at this boundary.

The ``asteroids`` table holds one row per asteroid with its newest close
approach. Which approach is newest is decided by :func:`domain.latest`; the
``WHERE`` guard in :data:`UPSERT_SQL` enforces the same rule at storage level,
so an older or concurrent run can never overwrite newer data.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Final

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from config import DbConfig
from domain import AsteroidSummary, Observation, Overview, UpsertResult, latest
from errors import StorageError

__all__ = ["AsteroidRepository", "UpsertResult", "connect"]

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
)


def connect(
    config: DbConfig,
    *,
    connect_timeout: int = 10,
    application_name: str = "neo-pipeline",
    **kwargs: Any,
) -> psycopg.Connection:
    """Open a new connection (autocommit off). Use it as a context manager.

    Raises:
        StorageError: If the server cannot be reached or rejects the login.
    """
    logger.debug("Connecting to %s", config)
    try:
        return psycopg.connect(
            host=config.host,
            port=config.port,
            dbname=config.dbname,
            user=config.user,
            password=config.password,
            connect_timeout=connect_timeout,
            application_name=application_name,
            **kwargs,
        )
    except psycopg.Error as exc:
        raise StorageError(f"Could not connect to PostgreSQL at {config.host}:{config.port}: {exc}") from exc


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


class AsteroidRepository:
    """The ``asteroids`` table. Satisfies :class:`ports.AsteroidStore` and :class:`ports.AsteroidReader`."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    # ----------------------------------------------------------------- write

    def save(self, observations: Sequence[Observation]) -> UpsertResult:
        """Store the newest observation per asteroid, atomically and idempotently.

        Counts are per asteroid. All rows are written in one transaction:
        either every row is applied or none are.

        Raises:
            StorageError: The statement failed; the transaction was rolled back.
        """
        rows = [_row(obs) for obs in latest(observations)]
        if not rows:
            return UpsertResult()

        inserted = updated = 0
        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
                cur.executemany(UPSERT_SQL, rows, returning=True)
                # executemany(returning=True) exposes one result set per row.
                while True:
                    row = cur.fetchone()
                    if row is not None:
                        if row[0]:
                            inserted += 1
                        else:
                            updated += 1
                    if not cur.nextset():
                        break
        except psycopg.Error as exc:
            raise _storage_error("Upsert", exc) from exc

        return UpsertResult(inserted=inserted, updated=updated, unchanged=len(rows) - inserted - updated)

    def count(self) -> int:
        """Total number of asteroids stored."""
        return self.overview().total

    # ------------------------------------------------------------------ read

    def overview(self) -> Overview:
        """Totals over the whole table (not limited like :meth:`list_recent`)."""
        query = sql.SQL(
            "SELECT COUNT(*), COUNT(*) FILTER (WHERE is_potentially_hazardous), MAX(close_approach_date) FROM {}"
        ).format(sql.Identifier(TABLE))
        try:
            with self._conn.cursor() as cur:
                cur.execute(query)
                row = cur.fetchone()
        except psycopg.Error as exc:
            raise _storage_error("Overview", exc) from exc
        if row is None:  # an aggregate always returns one row
            return Overview(total=0, hazardous=0, latest_approach=None)
        return Overview(total=int(row[0]), hazardous=int(row[1]), latest_approach=row[2])

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
            with self._conn.cursor(row_factory=dict_row) as cur:
                cur.execute(query, (limit,))
                rows = cur.fetchall()
        except psycopg.Error as exc:
            raise _storage_error("Listing asteroids", exc) from exc
        return [_summary(r) for r in rows]


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
    )
