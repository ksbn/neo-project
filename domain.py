"""The domain: types and rules every layer agrees on. Imports nothing from this project.

Write side (ingestion):

* :class:`Asteroid` holds stable facts, :class:`CloseApproach` holds one event, and
  :class:`Observation` is what a feed reports for one asteroid on one day.
* :func:`latest` is the single owner of the rule "the newest close approach
  wins" for the one-row-per-asteroid view. (``database.UPSERT_SQL`` enforces the
  same rule at storage level, so concurrent or out-of-order runs can't break it.)
* :class:`FeedBatch`, :class:`InvalidObject` and :class:`UpsertResult` are the values
  the ports (``ports.py``) exchange.

Read side (dashboard): :class:`AsteroidSummary` and :class:`Overview`.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

# --------------------------------------------------------------------------- #
# Entities and value objects
# --------------------------------------------------------------------------- #


class _Strict(BaseModel):
    # Unlike the lenient NASA DTOs, a misspelled field is an error, not silently dropped.
    model_config = ConfigDict(extra="forbid", frozen=True)


class Asteroid(_Strict):
    """Facts about an asteroid that don't depend on a particular approach."""

    neo_reference_id: str
    name: str
    nasa_jpl_url: str | None = None
    absolute_magnitude_h: Decimal | None = None
    estimated_diameter_min_km: Decimal | None = None
    estimated_diameter_max_km: Decimal | None = None
    is_potentially_hazardous: bool
    is_sentry_object: bool = False


class CloseApproach(_Strict):
    """One pass of an asteroid near a body."""

    close_approach_date: date
    relative_velocity_km_s: Decimal
    miss_distance_km: Decimal
    orbiting_body: str


class Observation(_Strict):
    """What a feed reported for one asteroid on one feed day."""

    asteroid: Asteroid
    approach: CloseApproach | None = None

    @property
    def neo_reference_id(self) -> str:
        return self.asteroid.neo_reference_id

    @property
    def approach_date(self) -> date | None:
        return self.approach.close_approach_date if self.approach else None


def latest(observations: Iterable[Observation]) -> list[Observation]:
    """One observation per asteroid: the one with the newest close approach.

    An observation with an approach beats one without; on equal dates the later
    one in ``observations`` wins. Order of first appearance is preserved.
    """
    newest: dict[str, Observation] = {}
    for obs in observations:
        current = newest.get(obs.neo_reference_id)
        if current is None or _not_older(obs, current):
            newest[obs.neo_reference_id] = obs
    return list(newest.values())


def _not_older(candidate: Observation, current: Observation) -> bool:
    if candidate.approach_date is None:
        return current.approach_date is None
    return current.approach_date is None or candidate.approach_date >= current.approach_date


# --------------------------------------------------------------------------- #
# Values exchanged through the ports
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class InvalidObject:
    """An object the feed sent that failed validation and was skipped."""

    feed_date: date
    neo_reference_id: str | None
    error: str


@dataclass(frozen=True)
class FeedBatch:
    """Everything a feed returned for one window, validated but not de-duplicated."""

    observations: list[Observation] = field(default_factory=list)
    invalid: list[InvalidObject] = field(default_factory=list)
    element_count: int = 0
    # Dates present in the response (used to detect missing days).
    feed_dates: frozenset[date] = frozenset()

    @property
    def unique_asteroids(self) -> int:
        return len({obs.neo_reference_id for obs in self.observations})


@dataclass(frozen=True)
class UpsertResult:
    """Row counts from one store write, per asteroid."""

    inserted: int = 0
    updated: int = 0
    unchanged: int = 0

    @property
    def total(self) -> int:
        return self.inserted + self.updated + self.unchanged


def date_windows(start: date, end: date, max_days: int) -> Iterator[tuple[date, date]]:
    """Split an inclusive date range into consecutive windows of at most ``max_days`` days.

    >>> list(date_windows(date(2024, 1, 1), date(2024, 1, 10), 7))
    [(datetime.date(2024, 1, 1), datetime.date(2024, 1, 7)), (datetime.date(2024, 1, 8), datetime.date(2024, 1, 10))]
    """
    if end < start:
        raise ValueError(f"end date ({end}) is before start date ({start})")
    if max_days < 1:
        raise ValueError("max_days must be at least 1")

    window_start = start
    while window_start <= end:
        window_end = min(window_start + timedelta(days=max_days - 1), end)
        yield window_start, window_end
        window_start = window_end + timedelta(days=1)


# --------------------------------------------------------------------------- #
# Read side
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AsteroidSummary:
    """One dashboard row."""

    neo_reference_id: str
    name: str
    estimated_diameter_min_km: float | None
    estimated_diameter_max_km: float | None
    is_potentially_hazardous: bool
    close_approach_date: date | None
    miss_distance_km: float | None


@dataclass(frozen=True)
class Overview:
    """Totals over the whole data set, not just the rows that were loaded."""

    total: int
    hazardous: int
    latest_approach: date | None


@dataclass(frozen=True)
class AsteroidView:
    """What the dashboard shows: totals plus the newest rows."""

    overview: Overview
    rows: list[AsteroidSummary]

    @property
    def is_empty(self) -> bool:
        return self.overview.total == 0
