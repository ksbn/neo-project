"""The domain: types and rules every layer agrees on. Imports nothing from this project.

Write side (ingestion):

* :class:`Asteroid` holds stable facts, :class:`CloseApproach` holds one event, and
  :class:`Observation` is what a feed reports for one asteroid on one day.
* :func:`latest` is the single owner of the rule "the newest close approach
  wins" for the one-row-per-asteroid view. (``infrastructure.repository.UPSERT_SQL`` enforces the
  same rule at storage level, so concurrent or out-of-order runs can't break it.)
* :class:`FeedBatch`, :class:`InvalidObject` and :class:`UpsertResult` are the values
  the ports (``application/ports.py``) exchange.

Runs: :class:`IngestionRun` records what one pipeline run did; :class:`PipelineStatus`
is what the dashboard needs to know about recent runs.

Read side (dashboard): :class:`AsteroidSummary`, :class:`ApproachSummary`,
:class:`Overview` and :class:`AsteroidView`.

Control Room: :class:`AnomalyAlert` and :class:`Severity` (rules in
``domain/anomaly_rules.py``); :class:`OrbitalElements`, :class:`Vec3` and
:class:`Trajectory3D` (math in ``domain/orbital_math.py``).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import IntEnum, StrEnum
from typing import NamedTuple, Self

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
    """Row counts from one store write: per asteroid, plus history rows added."""

    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    new_approaches: int = 0

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
# Pipeline runs
# --------------------------------------------------------------------------- #


class RunStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"  # some windows were committed before a failure
    FAILED = "failed"  # nothing was committed


@dataclass(frozen=True)
class IngestionRun:
    """What one pipeline run did. ``error_category`` is the NeoError class name, if it failed."""

    started_at: datetime
    finished_at: datetime
    start_date: date
    end_date: date
    status: RunStatus
    windows_total: int = 0
    windows_done: int = 0
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    invalid: int = 0
    missing_dates: int = 0
    new_approaches: int = 0
    error_category: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class PipelineStatus:
    """The latest run (whatever its outcome) and when data was last refreshed successfully."""

    last_run: IngestionRun | None
    last_success_at: datetime | None


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
    relative_velocity_km_s: float | None = None


@dataclass(frozen=True)
class ApproachSummary:
    """One recorded close approach of an asteroid."""

    close_approach_date: date
    orbiting_body: str
    relative_velocity_km_s: float
    miss_distance_km: float


@dataclass(frozen=True)
class Overview:
    """Totals over the whole data set, not just the rows that were loaded."""

    total: int
    hazardous: int
    latest_approach: date | None
    approaches: int = 0  # close approaches recorded (history), across all asteroids
    closest_miss_ld: float | None = None  # closest recorded pass, in lunar distances
    fastest_km_s: float | None = None  # fastest recorded pass


@dataclass(frozen=True)
class AsteroidView:
    """What the dashboard shows: totals plus the newest rows."""

    overview: Overview
    rows: list[AsteroidSummary]
    # Only the stored view has run records and per-asteroid history; the live fallback doesn't.
    status: PipelineStatus | None = None
    history_available: bool = False
    alerts: list[AnomalyAlert] = field(default_factory=list)  # most urgent first
    trend: list[tuple[date, int]] = field(default_factory=list)  # close approaches per day, oldest first

    @property
    def is_empty(self) -> bool:
        return self.overview.total == 0


# --------------------------------------------------------------------------- #
# Control Room: anomaly alerts
# --------------------------------------------------------------------------- #


class Severity(IntEnum):
    """Ordered: a higher value is more urgent."""

    INFO = 1
    WATCH = 2
    WARNING = 3
    CRITICAL = 4


@dataclass(frozen=True)
class AnomalyAlert:
    """One rule firing for one close approach (or for the asteroid, if it has none)."""

    rule: str
    severity: Severity
    neo_reference_id: str
    name: str
    close_approach_date: date | None
    message: str
    value: float | None = None  # the measured quantity, in the rule's unit
    threshold: float | None = None

    @classmethod
    def for_observation(
        cls,
        obs: Observation,
        rule: str,
        severity: Severity,
        message: str,
        value: float | None = None,
        threshold: float | None = None,
    ) -> Self:
        return cls(
            rule=rule, severity=severity, neo_reference_id=obs.neo_reference_id, name=obs.asteroid.name,
            close_approach_date=obs.approach_date, message=message, value=value, threshold=threshold,
        )


# --------------------------------------------------------------------------- #
# 3D visualizer: orbits and trajectories
# --------------------------------------------------------------------------- #


class Vec3(NamedTuple):
    """A point or direction in the J2000 ecliptic frame (x towards the March equinox, z to the ecliptic pole)."""

    x: float
    y: float
    z: float


@dataclass(frozen=True)
class OrbitalElements:
    """Osculating Keplerian elements of a heliocentric elliptical orbit at ``epoch_jd``.

    Angles are in degrees, referred to the J2000 ecliptic and equinox (as NeoWs
    ``/neo/{id}`` ``orbital_data`` reports them).
    """

    semi_major_axis_au: float
    eccentricity: float
    inclination_deg: float
    ascending_node_deg: float
    perihelion_argument_deg: float
    mean_anomaly_deg: float
    epoch_jd: float

    def __post_init__(self) -> None:
        if not self.semi_major_axis_au > 0:
            raise ValueError(f"semi_major_axis_au must be positive, got {self.semi_major_axis_au}")
        if not 0 <= self.eccentricity < 1:
            raise ValueError(f"eccentricity must be in [0, 1) for an elliptical orbit, got {self.eccentricity}")


@dataclass(frozen=True)
class Trajectory3D:
    """An asteroid's path around a close approach, centred on Earth, in lunar distances.

    Direction comes from the orbit model; the distance at closest approach is
    NASA's reported miss distance. ``model_miss_ld`` is what the two-body model
    alone predicted, which shows how approximate the drawn path is.
    """

    times_jd: list[float]
    points_ld: list[Vec3]
    closest_index: int  # nearest sample to Earth
    closest_time_jd: float  # exact time of closest approach in the model
    closest_point_ld: Vec3  # exact closest point, at NASA's miss distance
    miss_distance_ld: float
    model_miss_ld: float


@dataclass(frozen=True)
class OrbitData:
    """An asteroid's orbit as NASA determined it (``/neo/{id}`` ``orbital_data``)."""

    neo_reference_id: str
    elements: OrbitalElements
    orbit_id: str | None = None  # JPL solution number; changes when NASA refines the orbit
    orbit_class: str | None = None  # e.g. APO, ATE, AMO
    moid_au: float | None = None  # minimum orbit intersection distance with Earth


@dataclass(frozen=True)
class CachedOrbit:
    """An orbit in the local cache, with when it was fetched from NASA."""

    orbit: OrbitData
    fetched_at: datetime


class OrbitOrigin(StrEnum):
    CACHE = "cache"  # fresh enough local copy
    NASA = "nasa"  # fetched just now
    STALE_CACHE = "stale_cache"  # old local copy, because NASA could not be reached


@dataclass(frozen=True)
class OrbitLookup:
    """The orbit to draw, where it came from, and anything the viewer should know about it."""

    orbit: OrbitData
    fetched_at: datetime
    origin: OrbitOrigin
    note: str | None = None
