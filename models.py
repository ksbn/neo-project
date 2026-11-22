"""Pydantic models for parsing and validating NASA NeoWs feed responses.

Two layers of models:

* **Raw models** (``NearEarthObject`` and its children) mirror the JSON returned
  by ``/feed``. Numeric strings such as ``"12.345"`` are coerced to ``Decimal``.
* :func:`to_observation` maps them onto the domain's :class:`domain.Observation`
  (an :class:`domain.Asteroid` plus its :class:`domain.CloseApproach`).

This module is the anti-corruption layer for NASA's payload: nothing outside it
knows the raw JSON shape, and a malformed envelope surfaces as
:class:`errors.FeedContractError` rather than a pydantic exception.

``parse_feed`` validates each asteroid independently, so one malformed object
is reported and skipped instead of failing the whole batch. It does *not*
de-duplicate: deciding which approach wins is the domain's job (:func:`domain.latest`).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

import domain
from domain import Asteroid, FeedBatch, InvalidObject, Observation
from errors import FeedContractError

__all__ = ["parse_feed", "to_observation"]

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


class _IngestModel(BaseModel):
    """Base for raw NASA DTOs only: ignore fields we don't use, strip strings, immutable instances."""

    model_config = ConfigDict(extra="ignore", frozen=True, str_strip_whitespace=True)


# --------------------------------------------------------------------------- #
# Raw API models
# --------------------------------------------------------------------------- #


class DiameterRange(_IngestModel):
    """Min/max estimated diameter in a single unit."""

    estimated_diameter_min: Decimal = Field(ge=0, lt=DIAMETER_KM_LIMIT)
    estimated_diameter_max: Decimal = Field(ge=0, lt=DIAMETER_KM_LIMIT)

    @model_validator(mode="after")
    def _min_not_above_max(self) -> Self:
        if self.estimated_diameter_min > self.estimated_diameter_max:
            raise ValueError("estimated_diameter_min is greater than estimated_diameter_max")
        return self


class EstimatedDiameter(_IngestModel):
    """Diameter estimates per unit; only kilometers are stored."""

    kilometers: DiameterRange | None = None


class RelativeVelocity(_IngestModel):
    kilometers_per_second: Decimal = Field(ge=0, lt=VELOCITY_KM_S_LIMIT)


class MissDistance(_IngestModel):
    kilometers: Decimal = Field(ge=0, lt=MISS_DISTANCE_KM_LIMIT)


class CloseApproach(_IngestModel):
    """One entry of ``close_approach_data``."""

    close_approach_date: date
    relative_velocity: RelativeVelocity
    miss_distance: MissDistance
    orbiting_body: str = Field(min_length=1)


class NearEarthObject(_IngestModel):
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


class FeedResponse(_IngestModel):
    """Envelope of the ``/feed`` response.

    Asteroids are kept as raw values here and validated one by one in
    :func:`parse_feed`, so a single bad object doesn't reject the whole response.
    """

    element_count: int = Field(ge=0)
    near_earth_objects: dict[date, list[Any]]


# --------------------------------------------------------------------------- #
# Mapping to the domain record
# --------------------------------------------------------------------------- #


def to_observation(neo: NearEarthObject, feed_date: date) -> Observation:
    """Map a validated NEO onto the domain, using its close approach for ``feed_date``."""
    km = neo.estimated_diameter.kilometers if neo.estimated_diameter else None
    raw_approach = neo.approach_nearest(feed_date)
    asteroid = Asteroid(
        neo_reference_id=neo.neo_reference_id,
        name=neo.name,
        nasa_jpl_url=neo.nasa_jpl_url,
        absolute_magnitude_h=neo.absolute_magnitude_h,
        estimated_diameter_min_km=km.estimated_diameter_min if km else None,
        estimated_diameter_max_km=km.estimated_diameter_max if km else None,
        is_potentially_hazardous=neo.is_potentially_hazardous_asteroid,
        is_sentry_object=neo.is_sentry_object,
    )
    approach = (
        domain.CloseApproach(  # not the raw DTO of the same name below
            close_approach_date=raw_approach.close_approach_date,
            relative_velocity_km_s=raw_approach.relative_velocity.kilometers_per_second,
            miss_distance_km=raw_approach.miss_distance.kilometers,
            orbiting_body=raw_approach.orbiting_body,
        )
        if raw_approach
        else None
    )
    return Observation(asteroid=asteroid, approach=approach)


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def _summarize(exc: ValidationError) -> str:
    """Compact one-line summary of a ValidationError, e.g. ``name: Field required``."""
    return "; ".join(f"{'.'.join(map(str, err['loc'])) or '<root>'}: {err['msg']}" for err in exc.errors())


def parse_feed(payload: Mapping[str, Any]) -> FeedBatch:
    """Validate a raw ``/feed`` payload into domain observations, in feed-date order.

    The same asteroid may appear under several dates; every appearance is kept.

    Raises:
        FeedContractError: If the response envelope itself is malformed.
    """
    try:
        feed = FeedResponse.model_validate(payload)
    except ValidationError as exc:
        raise FeedContractError(f"NeoWs /feed response did not match the expected format: {_summarize(exc)}") from exc

    observations: list[Observation] = []
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

            observations.append(to_observation(neo, feed_date))

    if seen != feed.element_count:
        logger.warning("Feed element_count is %d but %d objects were present", feed.element_count, seen)

    return FeedBatch(
        observations=observations,
        invalid=invalid,
        element_count=seen,
        feed_dates=frozenset(feed.near_earth_objects),
    )
