"""Pydantic models for parsing and validating NASA NeoWs feed responses.

Two layers of models:

* **Raw models** (``NearEarthObject`` and its children) mirror the JSON returned
  by ``/feed``. Numeric strings such as ``"12.345"`` are coerced to ``Decimal``.
* :func:`to_observation` maps them onto the domain's :class:`domain.models.Observation`
  (an :class:`domain.models.Asteroid` plus its :class:`domain.models.CloseApproach`).

This module is the anti-corruption layer for NASA's payload: nothing outside it
knows the raw JSON shape, and a malformed envelope surfaces as
:class:`domain.errors.FeedContractError` rather than a pydantic exception.

``parse_feed`` validates each asteroid independently, so one malformed object
is reported and skipped instead of failing the whole batch. It does *not*
de-duplicate: deciding which approach wins is the domain's job (:func:`domain.models.latest`).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, ValidationError, model_validator

from planetary_defense.domain import models as domain
from planetary_defense.domain.errors import FeedContractError, OrbitUnavailable
from planetary_defense.domain.models import Asteroid, FeedBatch, InvalidObject, Observation, OrbitalElements, OrbitData

__all__ = ["parse_feed", "parse_orbit", "to_observation"]

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


# --------------------------------------------------------------------------- #
# /neo/{id}: orbital data
# --------------------------------------------------------------------------- #


class _OrbitClass(_IngestModel):
    orbit_class_type: str | None = None


class _OrbitalData(_IngestModel):
    """``orbital_data`` of ``/neo/{id}``. NASA sends numbers as strings, often like ``".41"``.

    Elements are optional here so a missing one is reported as "no usable orbit"
    (:class:`OrbitUnavailable`), while a present-but-garbled one is a contract
    break (:class:`FeedContractError`).
    """

    semi_major_axis: FiniteFloat | None = None
    eccentricity: FiniteFloat | None = None
    inclination: FiniteFloat | None = None
    ascending_node_longitude: FiniteFloat | None = None
    perihelion_argument: FiniteFloat | None = None
    mean_anomaly: FiniteFloat | None = None
    epoch_osculation: FiniteFloat | None = None
    orbit_id: str | None = None
    orbit_class: _OrbitClass | None = None
    minimum_orbit_intersection: FiniteFloat | None = None


class _NeoDetail(_IngestModel):
    neo_reference_id: str = Field(min_length=1)
    orbital_data: _OrbitalData | None = None


def parse_orbit(payload: Mapping[str, Any]) -> OrbitData:
    """Validate a raw ``/neo/{id}`` payload into the domain's :class:`OrbitData`.

    Raises:
        FeedContractError: The response doesn't have the documented shape.
        OrbitUnavailable: NASA has no complete elliptical orbit for this asteroid.
    """
    try:
        detail = _NeoDetail.model_validate(payload)
    except ValidationError as exc:
        raise FeedContractError(f"NeoWs /neo response did not match the expected format: {_summarize(exc)}") from exc

    ref, od = detail.neo_reference_id, detail.orbital_data
    if od is None:
        raise OrbitUnavailable(f"NASA has no orbital elements for asteroid {ref}")
    elements = {
        "semi_major_axis_au": od.semi_major_axis,
        "eccentricity": od.eccentricity,
        "inclination_deg": od.inclination,
        "ascending_node_deg": od.ascending_node_longitude,
        "perihelion_argument_deg": od.perihelion_argument,
        "mean_anomaly_deg": od.mean_anomaly,
        "epoch_jd": od.epoch_osculation,
    }
    missing = [name for name, field in _NASA_FIELD.items() if elements[field] is None]
    if missing:
        raise OrbitUnavailable(f"NASA's orbit for asteroid {ref} lacks {', '.join(missing)}")

    try:
        orbital_elements = OrbitalElements(**{k: float(v) for k, v in elements.items() if v is not None})
    except ValueError as exc:  # e.g. a hyperbolic orbit (eccentricity >= 1)
        raise OrbitUnavailable(f"The orbit of asteroid {ref} can't be drawn: {exc}") from exc

    return OrbitData(
        neo_reference_id=ref,
        elements=orbital_elements,
        orbit_id=od.orbit_id,
        orbit_class=od.orbit_class.orbit_class_type if od.orbit_class else None,
        moid_au=od.minimum_orbit_intersection,
    )


# NASA's field name -> OrbitalElements field, for "lacks ..." messages in NASA's terms.
_NASA_FIELD = {
    "semi_major_axis": "semi_major_axis_au",
    "eccentricity": "eccentricity",
    "inclination": "inclination_deg",
    "ascending_node_longitude": "ascending_node_deg",
    "perihelion_argument": "perihelion_argument_deg",
    "mean_anomaly": "mean_anomaly_deg",
    "epoch_osculation": "epoch_jd",
}
