"""Pydantic models for parsing and validating NASA NeoWs feed responses.

Two layers of models:

* **Raw models** (``NearEarthObject`` and its children) mirror the JSON returned
  by ``/feed``. Numeric strings such as ``"12.345"`` are coerced to ``Decimal``.
* **``AsteroidRecord``** is the flattened, validated row that maps 1:1 onto the
  ``asteroids`` table defined in ``db/migrations``.

``parse_feed`` validates each asteroid independently, so one malformed object
is reported and skipped instead of failing the whole batch.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

logger = logging.getLogger(__name__)


def _numeric_limit(precision: int, scale: int) -> Decimal:
    """Smallest absolute value that overflows a PostgreSQL ``NUMERIC(precision, scale)`` column.

    Postgres rounds extra decimal places away (so those are fine) but rejects
    values with too many integer digits, failing the whole insert batch.
    Bounding them here rejects just the offending asteroid instead.
    Values are rounded half-up to ``scale`` digits, hence the ``0.5`` offset.
    """
    return Decimal(10) ** (precision - scale) - Decimal("0.5") * Decimal(10) ** -scale


# Keep in sync with the column types in db/migrations.
DIAMETER_KM_LIMIT = _numeric_limit(14, 6)  # estimated_diameter_*_km NUMERIC(14, 6)
VELOCITY_KM_S_LIMIT = _numeric_limit(14, 6)  # relative_velocity_km_s   NUMERIC(14, 6)
MISS_DISTANCE_KM_LIMIT = _numeric_limit(20, 3)  # miss_distance_km      NUMERIC(20, 3)
MAGNITUDE_LIMIT = _numeric_limit(6, 3)  # absolute_magnitude_h          NUMERIC(6, 3)


class _NasaModel(BaseModel):
    """Base model: ignore fields we don't use, strip strings, immutable instances."""

    model_config = ConfigDict(extra="ignore", frozen=True, str_strip_whitespace=True)


# --------------------------------------------------------------------------- #
# Raw API models
# --------------------------------------------------------------------------- #


class DiameterRange(_NasaModel):
    """Min/max estimated diameter in a single unit."""

    estimated_diameter_min: Decimal = Field(ge=0, lt=DIAMETER_KM_LIMIT)
    estimated_diameter_max: Decimal = Field(ge=0, lt=DIAMETER_KM_LIMIT)

    @model_validator(mode="after")
    def _min_not_above_max(self) -> Self:
        if self.estimated_diameter_min > self.estimated_diameter_max:
            raise ValueError("estimated_diameter_min is greater than estimated_diameter_max")
        return self


class EstimatedDiameter(_NasaModel):
    """Diameter estimates per unit; only kilometers are stored."""

    kilometers: DiameterRange | None = None


class RelativeVelocity(_NasaModel):
    kilometers_per_second: Decimal = Field(ge=0, lt=VELOCITY_KM_S_LIMIT)


class MissDistance(_NasaModel):
    kilometers: Decimal = Field(ge=0, lt=MISS_DISTANCE_KM_LIMIT)


class CloseApproach(_NasaModel):
    """One entry of ``close_approach_data``."""

    close_approach_date: date
    relative_velocity: RelativeVelocity
    miss_distance: MissDistance
    orbiting_body: str = Field(min_length=1)


class NearEarthObject(_NasaModel):
    """A single asteroid as returned inside ``near_earth_objects``."""

    neo_reference_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    nasa_jpl_url: str | None = None
    absolute_magnitude_h: Decimal | None = Field(default=None, gt=-MAGNITUDE_LIMIT, lt=MAGNITUDE_LIMIT)
    estimated_diameter: EstimatedDiameter | None = None
    is_potentially_hazardous_asteroid: bool
    is_sentry_object: bool = False
    close_approach_data: list[CloseApproach] = Field(default_factory=list)

    def approach_nearest(self, day: date) -> CloseApproach | None:
        """Return the close approach on (or closest to) ``day``, or None if there are none.

        The feed normally lists exactly one approach per asteroid per day, but
        the field is a list, so pick deterministically.
        """
        if not self.close_approach_data:
            return None
        return min(self.close_approach_data, key=lambda a: abs((a.close_approach_date - day).days))


class FeedResponse(_NasaModel):
    """Envelope of the ``/feed`` response.

    Asteroids are kept as raw values here and validated one by one in
    :func:`parse_feed`, so a single bad object doesn't reject the whole response.
    """

    element_count: int = Field(ge=0)
    near_earth_objects: dict[date, list[Any]]


# --------------------------------------------------------------------------- #
# Database-ready record
# --------------------------------------------------------------------------- #


class AsteroidRecord(_NasaModel):
    """Flattened asteroid row matching the ``asteroids`` table columns."""

    neo_reference_id: str
    name: str
    nasa_jpl_url: str | None
    absolute_magnitude_h: Decimal | None
    estimated_diameter_min_km: Decimal | None
    estimated_diameter_max_km: Decimal | None
    is_potentially_hazardous: bool
    is_sentry_object: bool
    close_approach_date: date | None
    relative_velocity_km_s: Decimal | None
    miss_distance_km: Decimal | None
    orbiting_body: str | None

    @classmethod
    def from_neo(cls, neo: NearEarthObject, feed_date: date) -> Self:
        """Flatten a validated NEO, using its close approach for ``feed_date``."""
        km = neo.estimated_diameter.kilometers if neo.estimated_diameter else None
        approach = neo.approach_nearest(feed_date)
        return cls(
            neo_reference_id=neo.neo_reference_id,
            name=neo.name,
            nasa_jpl_url=neo.nasa_jpl_url,
            absolute_magnitude_h=neo.absolute_magnitude_h,
            estimated_diameter_min_km=km.estimated_diameter_min if km else None,
            estimated_diameter_max_km=km.estimated_diameter_max if km else None,
            is_potentially_hazardous=neo.is_potentially_hazardous_asteroid,
            is_sentry_object=neo.is_sentry_object,
            close_approach_date=approach.close_approach_date if approach else None,
            relative_velocity_km_s=approach.relative_velocity.kilometers_per_second if approach else None,
            miss_distance_km=approach.miss_distance.kilometers if approach else None,
            orbiting_body=approach.orbiting_body if approach else None,
        )


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class InvalidObject:
    """An asteroid that failed validation and was skipped."""

    feed_date: date
    neo_reference_id: str | None
    error: str


@dataclass(frozen=True)
class ParseResult:
    """Outcome of :func:`parse_feed`."""

    records: list[AsteroidRecord] = field(default_factory=list)
    invalid: list[InvalidObject] = field(default_factory=list)
    element_count: int = 0
    # Dates present as keys in near_earth_objects (used to detect missing days).
    feed_dates: frozenset[date] = frozenset()


def _summarize(exc: ValidationError) -> str:
    """Compact one-line summary of a ValidationError, e.g. ``name: Field required``."""
    return "; ".join(f"{'.'.join(map(str, err['loc'])) or '<root>'}: {err['msg']}" for err in exc.errors())


def _is_newer(candidate: AsteroidRecord, current: AsteroidRecord) -> bool:
    """True if ``candidate`` describes a later (or equal) close approach than ``current``."""
    if candidate.close_approach_date is None:
        return current.close_approach_date is None
    return current.close_approach_date is None or candidate.close_approach_date >= current.close_approach_date


def parse_feed(payload: Mapping[str, Any]) -> ParseResult:
    """Validate a raw ``/feed`` payload and flatten it into database records.

    The same asteroid may appear under several dates in one feed; it is
    de-duplicated, keeping its most recent close approach (matching the
    one-row-per-asteroid table design).

    Raises:
        ValidationError: If the response envelope itself is malformed.
    """
    feed = FeedResponse.model_validate(payload)

    latest: dict[str, AsteroidRecord] = {}
    invalid: list[InvalidObject] = []
    seen = 0

    for feed_date in sorted(feed.near_earth_objects):
        for raw in feed.near_earth_objects[feed_date]:
            seen += 1
            try:
                neo = NearEarthObject.model_validate(raw)
            except ValidationError as exc:
                ref = raw.get("neo_reference_id") if isinstance(raw, dict) else None
                invalid.append(InvalidObject(feed_date, str(ref) if ref is not None else None, _summarize(exc)))
                continue

            record = AsteroidRecord.from_neo(neo, feed_date)
            current = latest.get(record.neo_reference_id)
            if current is None or _is_newer(record, current):
                latest[record.neo_reference_id] = record

    if seen != feed.element_count:
        logger.warning("Feed element_count is %d but %d objects were present", feed.element_count, seen)

    return ParseResult(
        records=list(latest.values()),
        invalid=invalid,
        element_count=seen,
        feed_dates=frozenset(feed.near_earth_objects),
    )
