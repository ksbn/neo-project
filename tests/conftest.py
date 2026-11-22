"""Shared fixtures: an isolated PostgreSQL schema (builders live in support.py, fakes in fakes.py).

Database tests run against the PostgreSQL configured in .env (the Docker
container), inside a throwaway schema built from db/migrations. Every test runs
in a transaction that is always rolled back, and the schema is dropped at the
end of the session, so real data is never touched. If the database is not
reachable, database tests are skipped rather than failed.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import LiteralString, cast

import psycopg
import pytest
from psycopg import sql

from planetary_defense.config import DbConfig, Settings
from planetary_defense.domain.errors import ConfigError, StorageError
from planetary_defense.infrastructure.db import connect

from support import migration_up_sql

# --------------------------------------------------------------------------- #
# Database fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def db_schema() -> Iterator[tuple[DbConfig, str]]:
    """A throwaway schema with all migrations applied; dropped after the session."""
    try:
        cfg = Settings.load().db()
        admin = connect(cfg, connect_timeout=3, autocommit=True)
    except (ConfigError, StorageError) as exc:
        pytest.skip(f"PostgreSQL not available (start it with `docker compose up -d`): {exc}")

    name = f"test_{uuid.uuid4().hex[:8]}"
    with admin:
        schema = sql.Identifier(name)
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(schema))
        try:
            admin.execute(sql.SQL("SET search_path TO {}").format(schema))
            for up_sql in migration_up_sql():
                admin.execute(cast(LiteralString, up_sql))  # trusted: files from db/migrations in this repo
            yield cfg, name
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(schema))


@pytest.fixture
def conn(db_schema: tuple[DbConfig, str]) -> Iterator[psycopg.Connection]:
    """A connection to the test schema whose changes are always rolled back.

    The repository's own ``conn.transaction()`` becomes a savepoint inside this
    outer transaction, so even its "commits" are undone.
    """
    cfg, name = db_schema
    with connect(cfg, connect_timeout=3, options=f"-c search_path={name}") as c:
        with c.transaction(force_rollback=True):
            yield c
