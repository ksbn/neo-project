"""OrbitEnricher (application/enrich_orbit.py): cache-first /neo/{id} lookups and the 3D views.

Fakes only: an in-memory cache, a scripted NASA source and a fixed clock.
"""

import logging
import math
from datetime import UTC, date, datetime, timedelta

import pytest

from planetary_defense.application.enrich_orbit import OrbitEnricher
from planetary_defense.domain.errors import OrbitUnavailable, StorageError, UpstreamRateLimited, UpstreamUnavailable
from planetary_defense.domain.models import CachedOrbit, OrbitOrigin
from planetary_defense.domain.units import km_to_ld

from fakes import FakeOrbitSource, InMemoryOrbitCache, zoozve_orbit

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
ZOOZVE = "2524522"


def enricher(cache: InMemoryOrbitCache, source: FakeOrbitSource, max_age_days: int = 30) -> OrbitEnricher:
    return OrbitEnricher(cache, source, max_age=timedelta(days=max_age_days), clock=lambda: NOW)


def cached(age: timedelta) -> InMemoryOrbitCache:
    cache = InMemoryOrbitCache()
    cache.orbits[ZOOZVE] = CachedOrbit(orbit=zoozve_orbit(), fetched_at=NOW - age)
    return cache


# --------------------------------------------------------------------------- #
# Cache hit / miss
# --------------------------------------------------------------------------- #


def test_cache_hit_never_calls_nasa():
    source = FakeOrbitSource()
    lookup = enricher(cached(timedelta(days=2)), source).lookup(ZOOZVE)

    assert source.calls == []
    assert lookup.origin is OrbitOrigin.CACHE
    assert lookup.fetched_at == NOW - timedelta(days=2)
    assert lookup.orbit.elements.semi_major_axis_au == pytest.approx(0.72356318914361)
    assert lookup.note is None


def test_cache_miss_fetches_once_and_stores_the_orbit():
    cache, source = InMemoryOrbitCache(), FakeOrbitSource()
    first = enricher(cache, source).lookup(ZOOZVE)
    second = enricher(cache, source).lookup(ZOOZVE)

    assert source.calls == [ZOOZVE]  # the second lookup was a cache hit
    assert (first.origin, second.origin) == (OrbitOrigin.NASA, OrbitOrigin.CACHE)
    assert cache.orbits[ZOOZVE] == CachedOrbit(orbit=zoozve_orbit(), fetched_at=NOW)


def test_stale_cache_entry_is_refreshed():
    cache, source = cached(timedelta(days=31)), FakeOrbitSource()
    lookup = enricher(cache, source, max_age_days=30).lookup(ZOOZVE)

    assert source.calls == [ZOOZVE]
    assert lookup.origin is OrbitOrigin.NASA
    assert cache.orbits[ZOOZVE].fetched_at == NOW


# --------------------------------------------------------------------------- #
# Degrading gracefully
# --------------------------------------------------------------------------- #


def test_stale_orbit_is_served_when_nasa_is_unavailable():
    source = FakeOrbitSource(fail=UpstreamRateLimited("rate limit exceeded (HTTP 429)", status_code=429))
    lookup = enricher(cached(timedelta(days=45)), source).lookup(ZOOZVE)

    assert lookup.origin is OrbitOrigin.STALE_CACHE
    assert lookup.note is not None and "45 days" in lookup.note and "rate limit" in lookup.note


def test_miss_with_nasa_down_is_an_orbit_unavailable_error_with_the_cause():
    down = UpstreamUnavailable("Timed out calling NeoWs")
    with pytest.raises(OrbitUnavailable) as exc:
        enricher(InMemoryOrbitCache(), FakeOrbitSource(fail=down)).lookup(ZOOZVE)
    assert exc.value.__cause__ is down
    assert ZOOZVE in str(exc.value)


def test_nasa_having_no_orbit_is_passed_through():
    missing = OrbitUnavailable("NASA has no orbital elements for 999")
    with pytest.raises(OrbitUnavailable) as exc:
        enricher(InMemoryOrbitCache(), FakeOrbitSource(fail=missing)).lookup("999")
    assert exc.value is missing


def test_unreadable_cache_falls_back_to_nasa(caplog):
    cache, source = InMemoryOrbitCache(fail_get=StorageError("connection lost")), FakeOrbitSource()
    with caplog.at_level(logging.WARNING):
        lookup = enricher(cache, source).lookup(ZOOZVE)

    assert lookup.origin is OrbitOrigin.NASA and source.calls == [ZOOZVE]
    assert "Orbit cache unavailable" in caplog.text


def test_unwritable_cache_still_returns_nasas_orbit(caplog):
    cache = InMemoryOrbitCache(fail_put=StorageError("read-only"))
    with caplog.at_level(logging.WARNING):
        lookup = enricher(cache, FakeOrbitSource()).lookup(ZOOZVE)

    assert lookup.origin is OrbitOrigin.NASA
    assert lookup.note is not None and "not cached" in lookup.note
    assert "Could not cache" in caplog.text


def test_programmer_errors_are_not_swallowed():
    with pytest.raises(KeyError):
        enricher(InMemoryOrbitCache(), FakeOrbitSource(fail=KeyError("bug"))).lookup(ZOOZVE)


# --------------------------------------------------------------------------- #
# 3D views
# --------------------------------------------------------------------------- #


def test_earth_view_draws_the_approach_at_nasas_distance():
    """Zoozve's real elements and its 2026-11-02 pass (NASA: 15.65 LD)."""
    nasa_ld = km_to_ld(6_019_000)
    view = enricher(cached(timedelta(days=1)), FakeOrbitSource()).earth_view(ZOOZVE, date(2026, 11, 2), nasa_ld)

    assert view.lookup.origin is OrbitOrigin.CACHE
    assert math.dist(view.trajectory.closest_point_ld, (0, 0, 0)) == pytest.approx(nasa_ld)
    assert view.trajectory.model_miss_ld == pytest.approx(15.67, abs=0.05)  # the two-body model agrees closely


def test_sun_view_has_both_orbits_and_todays_positions():
    view = enricher(cached(timedelta(days=1)), FakeOrbitSource()).sun_view(ZOOZVE, date(2026, 11, 22), samples=90)

    assert len(view.asteroid_path) == len(view.earth_path) == 91
    sun_distances = [math.dist(p, (0, 0, 0)) for p in view.asteroid_path]
    assert min(sun_distances) == pytest.approx(0.4268, abs=1e-3)  # NASA's perihelion distance
    assert max(sun_distances) == pytest.approx(1.0203, abs=1e-3)  # NASA's aphelion distance
    assert 0.98 < math.dist(view.earth_position, (0, 0, 0)) < 1.02
    assert 0.42 < math.dist(view.asteroid_position, (0, 0, 0)) < 1.03
