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


# Zoozve (2002 VE68), as /neo/2524522 returned it on 2026-11-22: a real Aten orbit.
ZOOZVE_ORBITAL_DATA: dict[str, Any] = {
    "orbit_id": "111",
    "orbit_determination_date": "2026-11-18 04:49:39",
    "epoch_osculation": "2461200.5",
    "eccentricity": ".4101013489730946",
    "semi_major_axis": ".72356318914361",
    "inclination": "9.037338276017495",
    "ascending_node_longitude": "231.4572421200886",
    "perihelion_argument": "355.3998989862241",
    "mean_anomaly": "288.8364601767468",
    "mean_motion": "1.601361479792905",
    "minimum_orbit_intersection": ".025508",
    "perihelion_distance": ".4268289492085411",
    "aphelion_distance": "1.020297429078679",
    "equinox": "J2000",
    "orbit_class": {
        "orbit_class_type": "ATE",
        "orbit_class_description": "Near-Earth asteroid orbits similar to that of 2062 Aten",
    },
}


def make_neo_detail(ref: str = "2524522", *, without_orbit: bool = False, **orbital_overrides: Any) -> dict[str, Any]:
    """A /neo/{id} response with Zoozve's orbit; keyword arguments override orbital_data fields."""
    detail: dict[str, Any] = {
        "id": ref,
        "neo_reference_id": ref,
        "name": "524522 Zoozve (2002 VE68)",
        "is_potentially_hazardous_asteroid": True,
        "close_approach_data": [],
    }
    if not without_orbit:
        detail["orbital_data"] = {**copy.deepcopy(ZOOZVE_ORBITAL_DATA), **orbital_overrides}
    return detail
