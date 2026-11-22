"""The REST API's use cases: list and inspect objects, and watch one. No HTTP, no SQL.

:class:`CatalogService` composes the ports; ``api/app.py`` turns its results and the
domain errors it raises into HTTP responses that follow ``openapi.yaml``.
"""

from __future__ import annotations

from typing import Final

from planetary_defense.application.ports import AsteroidReader, ObjectReader, OrbitCache, WatchlistStore
from planetary_defense.domain.catalog import (
    IdempotencyClaim,
    ObjectDetail,
    ObjectPage,
    ObjectQuery,
    WatchRequest,
    WatchResult,
)
from planetary_defense.domain.errors import NotFound

HISTORY_LIMIT: Final = 50
WATCH_SCOPE: Final = "POST /watchlist"


class CatalogService:
    def __init__(self, objects: ObjectReader, history: AsteroidReader, orbits: OrbitCache,
                 watchlist: WatchlistStore) -> None:
        self._objects = objects
        self._history = history
        self._orbits = orbits
        self._watchlist = watchlist

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
