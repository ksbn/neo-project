"""PostgreSQL access layer for asteroid records.

Connection settings come from ``DB_HOST``, ``DB_PORT``, ``DB_NAME``, ``DB_USER``
and ``DB_PASSWORD`` (loaded from ``.env``). Writes go through
:class:`AsteroidRepository`, whose upsert is idempotent: running it twice with
the same data leaves the table unchanged.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final, Self

import psycopg
from dotenv import load_dotenv
from psycopg import sql

from models import AsteroidRecord

logger = logging.getLogger(__name__)

TABLE: Final = "asteroids"
KEY_COLUMN: Final = "neo_reference_id"
# Every column written by the pipeline; must match AsteroidRecord's fields.
COLUMNS: Final = tuple(AsteroidRecord.model_fields)
UPDATE_COLUMNS: Final = tuple(c for c in COLUMNS if c != KEY_COLUMN)


@dataclass(frozen=True)
class DbConfig:
    """PostgreSQL connection settings."""

    host: str
    port: int
    dbname: str
    user: str
    password: str = field(repr=False)  # keep the password out of logs/tracebacks

    @classmethod
    def from_env(cls) -> Self:
        """Build a config from environment variables (``.env`` is loaded first).

        Raises:
            ValueError: If a required variable is missing or DB_PORT is not an integer.
        """
        load_dotenv()
        required = ("DB_NAME", "DB_USER", "DB_PASSWORD")
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise ValueError(f"Missing database settings in environment/.env: {', '.join(missing)}")

        port_raw = os.getenv("DB_PORT", "5432")
        try:
            port = int(port_raw)
        except ValueError:
            raise ValueError(f"DB_PORT must be an integer, got {port_raw!r}") from None

        return cls(
            host=os.getenv("DB_HOST", "localhost"),
            port=port,
            dbname=os.environ["DB_NAME"],
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],
        )


def connect(config: DbConfig | None = None, *, connect_timeout: int = 10) -> psycopg.Connection:
    """Open a new connection (autocommit off). Use it as a context manager.

    Raises:
        psycopg.OperationalError: If the server cannot be reached or rejects the login.
    """
    config = config or DbConfig.from_env()
    logger.debug("Connecting to %s", config)
    return psycopg.connect(
        host=config.host,
        port=config.port,
        dbname=config.dbname,
        user=config.user,
        password=config.password,
        connect_timeout=connect_timeout,
        application_name="neo-pipeline",
    )


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
        """
        if not records:
            return UpsertResult()

        params = [record.model_dump() for record in records]
        inserted = updated = 0

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

        return UpsertResult(inserted=inserted, updated=updated, unchanged=len(records) - inserted - updated)

    def count(self) -> int:
        """Total number of asteroids stored."""
        with self._conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(TABLE)))
            row = cur.fetchone()
        return int(row[0]) if row else 0
