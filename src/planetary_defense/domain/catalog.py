"""The object catalog and the watchlist, as the REST API (``openapi.yaml``) sees them. Pure types.

Read side: :class:`ObjectQuery` (filters, sort and page), :class:`ObjectRow` (one listed
object with its latest pass and worst alert) and :class:`ObjectDetail` (everything about one
object). Write side: :class:`WatchRequest` and the stored :class:`WatchlistEntry`.

Unknown values are ``None``, never ``0``: the API serialises them as ``null``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Final, Literal

from planetary_defense.domain.models import AnomalyAlert, ApproachSummary, CachedOrbit, Severity

SortKey = Literal["-approach_date", "approach_date", "distance", "-distance", "-velocity", "velocity", "-diameter",
                  "name"]
MAX_PAGE: Final = 100


@dataclass(frozen=True)
class ObjectQuery:
    """Filters (combined with AND), a stable sort and one page."""

    q: str | None = None
    hazardous: bool | None = None
    max_distance_ld: float | None = None
    min_velocity_km_s: float | None = None
    min_severity: Severity | None = None
    approach_from: date | None = None
    approach_to: date | None = None
    watched: bool | None = None  # ADR 0002
    sort: SortKey = "-approach_date"
    limit: int = 25
    offset: int = 0

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= MAX_PAGE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE}")
        if self.offset < 0:
            raise ValueError("offset must not be negative")
        if self.approach_from and self.approach_to and self.approach_from > self.approach_to:
            raise ValueError("approach_from is after approach_to")


@dataclass(frozen=True)
class ObjectRow:
    """One listed object: stable facts, its latest pass, and the worst alert raised for that pass."""

    neo_reference_id: str
    name: str
    is_potentially_hazardous: bool
    is_sentry_object: bool
    absolute_magnitude_h: float | None
    estimated_diameter_min_km: float | None
    estimated_diameter_max_km: float | None
    latest_approach: ApproachSummary | None
    alert_level: Severity | None
    watched: bool = False  # ADR 0002


@dataclass(frozen=True)
class ObjectPage:
    rows: list[ObjectRow]
    total: int  # every match, not only this page


@dataclass(frozen=True)
class Summary:
    """Aggregates for the dashboard's KPIs (ADR 0001). Unknown or empty is None, never 0."""

    total_objects: int
    hazardous_objects: int
    watched_objects: int
    alert_counts: dict[Severity, int]  # objects by the worst alert on their latest pass; every level present
    closest: tuple[str, str, ApproachSummary] | None  # (id, name, pass)
    latest_approach_date: date | None
    last_sync_at: datetime | None
    approaches_per_day: list[tuple[date, int]]  # oldest first, zeros included


@dataclass(frozen=True)
class ObjectDetail:
    row: ObjectRow
    nasa_jpl_url: str | None
    orbit: CachedOrbit | None  # None: not in the local cache (the API never calls NASA for it)
    approach_history: list[ApproachSummary]  # newest first
    alerts: list[AnomalyAlert]  # most severe first
    watch: WatchlistEntry | None

    def next_predicted_approach(self, today: date) -> ApproachSummary | None:
        return self.orbit.orbit.next_approach(today) if self.orbit else None


# --------------------------------------------------------------------------- #
# Watchlist
# --------------------------------------------------------------------------- #


class Priority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class WatchRequest:
    """What an operator asks for when watching an object (already validated against the contract)."""

    neo_reference_id: str
    priority: Priority
    alert_threshold_ld: float | None = None
    note: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)

    def fingerprint(self) -> str:
        """SHA-256 of the canonical request: equal requests hash equal, whatever the JSON key order or spacing.

        Defaults are filled in before hashing, so ``{"priority": "low"}`` and
        ``{"priority": "low", "tags": []}`` are the same request.
        """
        canonical = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class WatchlistEntry:
    id: str
    neo_reference_id: str
    name: str
    priority: Priority
    alert_threshold_ld: float | None
    note: str | None
    tags: tuple[str, ...]
    created_at: datetime


@dataclass(frozen=True)
class WatchlistPage:
    entries: list[WatchlistEntry]
    total: int


@dataclass(frozen=True)
class WatchResult:
    """The entry, and whether it is a replay of an earlier request with the same idempotency key."""

    entry: WatchlistEntry
    replayed: bool = False


@dataclass(frozen=True)
class IdempotencyClaim:
    """An ``Idempotency-Key`` for one operation (``scope``), with the fingerprint of the request it came with."""

    scope: str
    key: str
    request_hash: str
