"""Fixtures for the REST API tests: seeded data in the throwaway schema, and a contract-checking client."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import nullcontext
from datetime import UTC, date, datetime

import psycopg
import pytest
from fastapi.testclient import TestClient

from planetary_defense.api.app import create_app
from planetary_defense.api.errors import RateLimiter
from planetary_defense.domain.anomaly_rules import default_rules, evaluate
from planetary_defense.domain.models import CachedOrbit
from planetary_defense.domain.units import LUNAR_DISTANCE_KM
from planetary_defense.infrastructure.nasa_dto import parse_feed
from planetary_defense.infrastructure.repository import AlertRepository, AsteroidRepository, OrbitRepository

from contract import ContractClient
from fakes import zoozve_orbit
from support import make_feed, make_neo

TODAY = date(2026, 11, 22)
ZOOZVE, KF54, NEAR, FAST, BARE = "2524522", "3076814", "3843271", "3999999", "2000433"


def neo(ref: str, name: str, day: str, ld: float, km_s: float, *, pha: bool = False,
        dmax_km: float | None = 0.2, h: float | None = 21.0) -> dict:
    """One feed object; ``dmax_km=None`` / ``h=None`` leave those fields out, as NASA sometimes does."""
    extra: dict = {"name": name, "is_potentially_hazardous_asteroid": pha, "absolute_magnitude_h": h,
                   "close_approach_data": [{
                       "close_approach_date": day, "orbiting_body": "Earth",
                       "relative_velocity": {"kilometers_per_second": str(km_s)},
                       "miss_distance": {"kilometers": str(round(ld * LUNAR_DISTANCE_KM, 3))}}]}
    extra["estimated_diameter"] = (
        {"kilometers": {"estimated_diameter_min": dmax_km / 2, "estimated_diameter_max": dmax_km}}
        if dmax_km is not None else None)
    return make_neo(ref, day, **extra)


SEED = [
    neo(ZOOZVE, "524522 Zoozve (2002 VE68)", "2026-11-02", 15.65, 8.7, pha=True, dmax_km=0.45, h=20.3),
    neo(KF54, "(2001 KF54)", "2026-11-06", 18.84, 12.0, pha=True, dmax_km=0.65),
    neo(NEAR, "(2026 WN1)", "2026-11-20", 0.8, 25.0, dmax_km=0.02),
    neo(FAST, "(2026 VF9)", "2026-11-21", 50.0, 35.0),
    neo(BARE, "(2026 BARE)", "2026-11-10", 100.0, 5.0, dmax_km=None, h=None),
]


def seed(conn: psycopg.Connection) -> None:
    """The objects above through the real ingest path, scored by the real rules; Zoozve's orbit cached."""
    by_day: dict[str, list] = {}
    for n in SEED:
        by_day.setdefault(n["close_approach_data"][0]["close_approach_date"], []).append(n)
    observations = parse_feed(make_feed(by_day)).observations
    AsteroidRepository(conn).save(observations)
    AlertRepository(conn).replace_alerts([(o.neo_reference_id, o.approach_date) for o in observations
                                          if o.approach_date], evaluate(observations, default_rules()))
    OrbitRepository(conn).put_orbit(CachedOrbit(zoozve_orbit(), datetime(2026, 11, 22, 8, 0, tzinfo=UTC)))


def app_for(conn: psycopg.Connection, **limits: RateLimiter):
    return create_app(lambda: nullcontext(conn), today=lambda: TODAY,
                      read_limiter=limits.get("reads", RateLimiter(10_000, 1_000)),
                      write_limiter=limits.get("writes", RateLimiter(10_000, 1_000)))


@pytest.fixture
def seeded(conn: psycopg.Connection) -> psycopg.Connection:
    seed(conn)
    return conn


@pytest.fixture
def api(seeded: psycopg.Connection) -> Iterator[ContractClient]:
    with TestClient(app_for(seeded), raise_server_exceptions=False) as client:
        yield ContractClient(client)
