"""Test data builders: NeoWs payloads shaped like the real API, and the migrations' SQL."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = ROOT / "db" / "migrations"

# api.data.gov's body for a rejected key (HTTP 403).
INVALID_KEY: dict[str, Any] = {"error": {"code": "API_KEY_INVALID", "message": "An invalid api_key was supplied."}}

_UP = re.compile(r"^-- migrate:up[^\n]*\n(.*?)(?=^-- migrate:down|\Z)", re.MULTILINE | re.DOTALL)


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


def migration_up_sql() -> list[str]:
    """The ``-- migrate:up`` section of every migration, in version order."""
    sections = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        match = _UP.search(path.read_text())
        if match is None:
            raise ValueError(f"{path.name} has no '-- migrate:up' section")
        sections.append(match.group(1))
    return sections
