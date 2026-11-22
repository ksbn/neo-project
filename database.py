"""PostgreSQL access layer for asteroid records.

Connection settings arrive as a :class:`config.DbConfig`; this module never
reads the environment. Reads and writes go through :class:`AsteroidRepository`,
whose upsert is idempotent: running it twice with the same data leaves the
table unchanged. Every ``psycopg.Error`` is translated to
:class:`errors.StorageError` at this boundary.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from config import DbConfig
from domain import AsteroidRecord
from errors import StorageError

__all__ = ["AsteroidRepository", "DbConfig", "SUMMARY_COLUMNS", "UpsertResult", "connect"]

logger = logging.getLogger(__name__)

TABLE: Final = "asteroids"
KEY_COLUMN: Final = "neo_reference_id"
# Every column written by the pipeline; must match AsteroidRecord's fields.
COLUMNS: Final = tuple(AsteroidRecord.model_fields)
UPDATE_COLUMNS: Final = tuple(c for c in COLUMNS if c != KEY_COLUMN)
# The columns the dashboard lists, newest close approach first.
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


@dataclass(frozen=True)
class UpsertResult:
    """Row counts from one :meth:`AsteroidRepository.upsert_many` call."""

    inserted: int = 0
    updated: int = 0
    unchanged: int = 0

    @property
    def total(self) -> int:
        return self.inserted + self.updated + self.unchanged


class AsteroidRepository:
    """Reads and writes rows in the ``asteroids`` table."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def upsert_many(self, records: Sequence[AsteroidRecord]) -> UpsertResult:
        """Insert new asteroids and update changed ones, atomically.

        All records are written in a single transaction: either every record
        is applied or none are.

        Raises:
            StorageError: The statement failed; the transaction was rolled back.
        """
        if not records:
            return UpsertResult()

        params = [record.model_dump() for record in records]
        inserted = updated = 0

        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
                cur.executemany(UPSERT_SQL, params, returning=True)
                # executemany(returning=True) exposes one result set per record.
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

        return UpsertResult(inserted=inserted, updated=updated, unchanged=len(records) - inserted - updated)

    def count(self) -> int:
        """Total number of asteroids stored."""
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(TABLE)))
                row = cur.fetchone()
        except psycopg.Error as exc:
            raise _storage_error("Count", exc) from exc
        return int(row[0]) if row else 0

    def list_recent(self, limit: int = 5_000) -> list[dict[str, Any]]:
        """The dashboard view: :data:`SUMMARY_COLUMNS`, newest close approach first, at most ``limit`` rows."""
        if limit < 1:
            raise ValueError("limit must be at least 1")
        query = sql.SQL(
            "SELECT {cols} FROM {table} "
            "ORDER BY close_approach_date DESC NULLS LAST, {key} "
            "LIMIT %s"
        ).format(
            cols=sql.SQL(", ").join(map(sql.Identifier, SUMMARY_COLUMNS)),
            table=sql.Identifier(TABLE),
            key=sql.Identifier(KEY_COLUMN),
        )
        try:
            with self._conn.cursor(row_factory=dict_row) as cur:
                cur.execute(query, (limit,))
                return cur.fetchall()
        except psycopg.Error as exc:
            raise _storage_error("Listing asteroids", exc) from exc
