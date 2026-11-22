"""Tier 2: lazy, cache-first orbit enrichment for the 3D views.

:meth:`OrbitEnricher.lookup` reads the local cache first and calls NASA's
``/neo/{id}`` only on a miss or when the cached copy is older than ``max_age``.
When NASA can't be reached it serves a stale copy rather than nothing, and a
broken cache never blocks a fresh fetch: failures degrade, they don't cascade.

:meth:`earth_view` and :meth:`sun_view` turn the orbit into the geometry the
two 3D views draw (see ``domain/orbital_math.py``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from planetary_defense.application.ports import OrbitCache, OrbitSource
from planetary_defense.domain.errors import NeoError, OrbitUnavailable, StorageError, UpstreamError
from planetary_defense.domain.models import (
    AnomalyAlert,
    CachedOrbit,
    OrbitLookup,
    OrbitOrigin,
    Severity,
    Trajectory3D,
    Vec3,
)
from planetary_defense.domain.orbital_math import (
    approach_trajectory,
    earth_elements,
    earth_position,
    heliocentric_position,
    julian_day,
    orbit_path,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_AGE = timedelta(days=30)


@dataclass(frozen=True)
class EarthView:
    """Earth-centred view: the approach path in lunar distances."""

    lookup: OrbitLookup
    trajectory: Trajectory3D


@dataclass(frozen=True)
class SunView:
    """Sun-centred view: both orbits and where the bodies are on ``on``, in AU."""

    lookup: OrbitLookup
    on_jd: float
    asteroid_path: list[Vec3]
    earth_path: list[Vec3]
    asteroid_position: Vec3
    earth_position: Vec3


def earth_view_from(
    lookup: OrbitLookup, approach: date | datetime, miss_distance_ld: float, *, window_days: int = 3
) -> EarthView:
    """The Earth-centred view of an orbit already looked up (pure geometry, no I/O)."""
    trajectory = approach_trajectory(lookup.orbit.elements, approach, miss_distance_ld, window_days=window_days)
    return EarthView(lookup=lookup, trajectory=trajectory)


def sun_view_from(lookup: OrbitLookup, on: date | datetime, *, samples: int = 360) -> SunView:
    """The Sun-centred view of an orbit already looked up, with positions on ``on`` (pure geometry, no I/O)."""
    jd = julian_day(on)
    elements = lookup.orbit.elements
    return SunView(
        lookup=lookup,
        on_jd=jd,
        asteroid_path=orbit_path(elements, samples),
        earth_path=orbit_path(earth_elements(jd), samples),
        asteroid_position=heliocentric_position(elements, jd),
        earth_position=earth_position(jd),
    )


class OrbitEnricher:
    def __init__(
        self,
        cache: OrbitCache,
        source: OrbitSource,
        *,
        max_age: timedelta = DEFAULT_MAX_AGE,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._cache = cache
        self._source = source
        self._max_age = max_age
        self._clock = clock

    def lookup(self, neo_reference_id: str) -> OrbitLookup:
        """The orbit of ``neo_reference_id``, from the cache when fresh, else from NASA.

        Raises:
            OrbitUnavailable: NASA has no usable orbit, or NASA failed and nothing is cached.
        """
        now = self._clock()
        cached = self._read_cache(neo_reference_id)
        if cached is not None and now - cached.fetched_at <= self._max_age:
            return OrbitLookup(cached.orbit, cached.fetched_at, OrbitOrigin.CACHE)

        try:
            orbit = self._source.fetch_orbit(neo_reference_id)
        except UpstreamError as exc:
            if cached is not None:
                age = (now - cached.fetched_at).days
                note = f"Showing an orbit cached {age} days ago: NASA could not be reached ({exc})."
                logger.warning("Serving stale orbit for %s: %s", neo_reference_id, exc)
                return OrbitLookup(cached.orbit, cached.fetched_at, OrbitOrigin.STALE_CACHE, note)
            raise OrbitUnavailable(
                f"No orbit for {neo_reference_id}: NASA could not be reached and none is cached"
            ) from exc

        fresh = CachedOrbit(orbit=orbit, fetched_at=now)
        try:
            self._cache.put_orbit(fresh)
        except StorageError as exc:
            logger.warning("Could not cache the orbit of %s: %s", neo_reference_id, exc)
            note = "Fetched from NASA but not cached; it will be fetched again."
            return OrbitLookup(orbit, now, OrbitOrigin.NASA, note)
        return OrbitLookup(orbit, now, OrbitOrigin.NASA)

    def earth_view(
        self, neo_reference_id: str, approach: date | datetime, miss_distance_ld: float, *, window_days: int = 3
    ) -> EarthView:
        return earth_view_from(self.lookup(neo_reference_id), approach, miss_distance_ld, window_days=window_days)

    def sun_view(self, neo_reference_id: str, on: date | datetime, *, samples: int = 360) -> SunView:
        return sun_view_from(self.lookup(neo_reference_id), on, samples=samples)

    def _read_cache(self, neo_reference_id: str) -> CachedOrbit | None:
        try:
            return self._cache.get_orbit(neo_reference_id)
        except StorageError as exc:
            logger.warning("Orbit cache unavailable, asking NASA instead: %s", exc)
            return None


# --------------------------------------------------------------------------- #
# Warming the cache for top-priority asteroids
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class WarmResult:
    fetched: int = 0  # asked NASA
    cached: int = 0  # already fresh in the cache
    failed: int = 0


def warm_orbits(
    enricher: OrbitEnricher,
    alerts: Iterable[AnomalyAlert],
    *,
    min_severity: Severity = Severity.WARNING,
    limit: int = 10,
) -> WarmResult:
    """Make sure the orbits of the most urgent alerts' asteroids are cached.

    So the 3D view of the asteroids people are most likely to open never waits
    on NASA. Most urgent first, each asteroid once, at most ``limit``. Best
    effort: operational failures are logged and counted, never raised.
    """
    ranked = sorted((a for a in alerts if a.severity >= min_severity), key=lambda a: -a.severity)
    targets = list(dict.fromkeys(a.neo_reference_id for a in ranked))[:limit]
    fetched = cached = failed = 0
    for neo_reference_id in targets:
        try:
            origin = enricher.lookup(neo_reference_id).origin
        except NeoError as exc:
            logger.warning("Could not warm the orbit of %s: %s", neo_reference_id, exc)
            failed += 1
            continue
        if origin is OrbitOrigin.CACHE:
            cached += 1
        elif origin is OrbitOrigin.NASA:
            fetched += 1
        else:  # STALE_CACHE: NASA could not refresh an old copy
            failed += 1
    if targets:
        logger.info("Orbit cache warmed for %d asteroids: %d fetched, %d already cached, %d failed",
                    len(targets), fetched, cached, failed)
    return WarmResult(fetched=fetched, cached=cached, failed=failed)
