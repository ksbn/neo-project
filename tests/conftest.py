"""Shared fixtures: NeoWs payload builders and an isolated PostgreSQL schema.

Database tests run against the PostgreSQL configured in .env (the Docker
container), inside a throwaway schema built from db/migrations. Every test runs
in a transaction that is always rolled back, and the schema is dropped at the
end of the session, so real data is never touched. If the database is not
reachable, database tests are skipped rather than failed.
"""

from __future__ import annotations

import copy
import re
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any, LiteralString, cast

import psycopg
import pytest
from psycopg import sql

from config import DbConfig, Settings
from database import connect
from errors import ConfigError, StorageError

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = ROOT / "db" / "migrations"

_UP = re.compile(r"^-- migrate:up[^\n]*\n(.*?)(?=^-- migrate:down|\Z)", re.MULTILINE | re.DOTALL)


# --------------------------------------------------------------------------- #
# Payload builders
# --------------------------------------------------------------------------- #


def make_neo(ref: str = "2000433", day: str = "2026-11-10", **overrides: Any) -> dict[str, Any]:
    """One asteroid shaped like a NeoWs feed entry (numeric strings as NASA sends them)."""
    neo: dict[str, Any] = {
        "id": ref,
        "neo_reference_id": ref,
        "name": f"{ref} (Test)",
        "nasa_jpl_url": f"https://ssd.jpl.nasa.gov/?sstr={ref}",
        "absolute_magnitude_h": 22.1,
        "estimated_diameter": {"kilometers": {"estimated_diameter_min": 0.1, "estimated_diameter_max": 0.25}},
        "is_potentially_hazardous_asteroid": False,
        "close_approach_data": [
            {
                "close_approach_date": day,
                "relative_velocity": {"kilometers_per_second": "12.345678"},
                "miss_distance": {"kilometers": "4567890.123"},
                "orbiting_body": "Earth",
            }
        ],
        "is_sentry_object": False,
    }
    neo.update(overrides)
    return neo


def make_feed(by_day: dict[str, list[Any]]) -> dict[str, Any]:
    """A /feed response envelope around ``{date: [asteroids]}``."""
    return {
        "element_count": sum(len(v) for v in by_day.values()),
        "near_earth_objects": copy.deepcopy(by_day),
    }


# --------------------------------------------------------------------------- #
# Database fixtures
# --------------------------------------------------------------------------- #


def migration_up_sql() -> list[str]:
    """The ``-- migrate:up`` section of every migration, in version order."""
    sections = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        match = _UP.search(path.read_text())
        if match is None:
            raise ValueError(f"{path.name} has no '-- migrate:up' section")
        sections.append(match.group(1))
    return sections


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
