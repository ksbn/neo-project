"""The REST API's use cases: list and inspect objects, and watch one. No HTTP, no SQL.

:class:`CatalogService` composes the ports; ``api/app.py`` turns its results and the
domain errors it raises into HTTP responses that follow ``openapi.yaml``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Final, Literal

from planetary_defense.application.enrich_orbit import DEFAULT_MAX_AGE, OrbitEnricher
from planetary_defense.application.ports import AsteroidReader, ObjectReader, OrbitCache, OrbitSource, WatchlistStore
from planetary_defense.application.viewport import pass_payload, provenance
from planetary_defense.domain.catalog import (
    IdempotencyClaim,
    ObjectDetail,
    ObjectPage,
    ObjectQuery,
    Summary,
    WatchlistPage,
    WatchRequest,
    WatchResult,
)
from planetary_defense.domain.errors import NeoError, NotFound, StorageError
from planetary_defense.domain.models import OrbitLookup, OrbitOrigin

logger = logging.getLogger(__name__)

HISTORY_LIMIT: Final = 50
WATCH_SCOPE: Final = "POST /watchlist"
PassChoice = Literal["latest", "next"]


@dataclass(frozen=True)
class TrajectoryResult:
    """The viewport's geometry document (None: nothing to draw) and where its orbit came from."""

    payload: dict[str, Any] | None
    orbit_origin: OrbitOrigin | None
    provenance: str


class CatalogService:
    def __init__(self, objects: ObjectReader, history: AsteroidReader, orbits: OrbitCache,
                 watchlist: WatchlistStore, *, orbit_source: OrbitSource | None = None,
                 orbit_max_age: timedelta = DEFAULT_MAX_AGE) -> None:
        self._objects = objects
        self._history = history
        self._orbits = orbits
        self._watchlist = watchlist
        self._orbit_source = orbit_source  # None: cache only, never NASA
        self._orbit_max_age = orbit_max_age

    def list_objects(self, query: ObjectQuery) -> ObjectPage:
        return self._objects.search(query)

    def get_object(self, neo_reference_id: str) -> ObjectDetail:
        """Everything about one object; the orbit comes from the local cache only (never NASA).

        Raises:
            NotFound: The object is not stored.
        """
        found = self._objects.find(neo_reference_id)
        if found is None:
            raise NotFound(f"No near-Earth object with ID {neo_reference_id} is stored.")
        row, url = found
        return ObjectDetail(
            row=row,
            nasa_jpl_url=url,
            orbit=self._orbits.get_orbit(neo_reference_id),
            approach_history=self._history.approach_history(neo_reference_id, HISTORY_LIMIT),
            alerts=self._objects.alerts_for(neo_reference_id),
            watch=self._watchlist.watch_of(neo_reference_id),
        )

    def watch(self, request: WatchRequest, idempotency_key: str | None = None) -> WatchResult:
        """Add the object to the watchlist; a retry with the same key and request replays the first result."""
        claim = (IdempotencyClaim(WATCH_SCOPE, idempotency_key, request.fingerprint())
                 if idempotency_key is not None else None)
        return self._watchlist.add(request, claim)

    def summary(self, today: date) -> Summary:
        return self._objects.summary(today)

    def list_watchlist(self, limit: int, offset: int) -> WatchlistPage:
        return self._watchlist.entries(limit, offset)

    def trajectory(self, neo_reference_id: str, which: PassChoice, today: date) -> TrajectoryResult:
        """The 3D geometry of the object's latest recorded pass, or its next predicted one (ADR 0003).

        The orbit is cache-first and fetched from NASA only on a miss (when a source is configured);
        without an orbit the latest pass is drawn as an approximate straight line.

        Raises:
            NotFound: The object is not stored.
            StorageError: The database failed.
        """
        found = self._objects.find(neo_reference_id)
        if found is None:
            raise NotFound(f"No near-Earth object with ID {neo_reference_id} is stored.")
        row, _ = found
        lookup, reason = self._orbit_lookup(neo_reference_id)
        if which == "next":
            approach = lookup.orbit.next_approach(today) if lookup is not None else None
        else:
            approach = row.latest_approach
        text = provenance(lookup, reason)
        payload = pass_payload(lookup, name=row.name, hazardous=row.is_potentially_hazardous, approach=approach,
                               provenance=text, today=today)
        return TrajectoryResult(payload, lookup.origin if lookup is not None else None, text)

    def _orbit_lookup(self, neo_reference_id: str) -> tuple[OrbitLookup | None, str | None]:
        """The orbit, or (None, why not). Operational failures degrade to "no orbit"; storage errors raise."""
        if self._orbit_source is None:
            cached = self._orbits.get_orbit(neo_reference_id)
            if cached is None:
                return None, "not in the local cache"
            return OrbitLookup(cached.orbit, cached.fetched_at, OrbitOrigin.CACHE), None
        try:
            return OrbitEnricher(self._orbits, self._orbit_source, max_age=self._orbit_max_age).lookup(
                neo_reference_id), None
        except StorageError:
            raise
        except NeoError as exc:
            logger.warning("No orbit for %s: %s", neo_reference_id, exc)
            return None, "NASA has none, or could not be reached"
