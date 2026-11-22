"""Domain record shared by ingestion, storage and the dashboard.

Deliberately independent of NASA's payload shape: the mapping from the raw
NeoWs objects lives in ``models.to_record``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class AsteroidRecord(BaseModel):
    """One asteroid with its latest close approach; maps 1:1 onto the ``asteroids`` table."""

    # Strict, unlike the lenient NASA DTOs: a misspelled field is an error, not silently dropped.
    model_config = ConfigDict(extra="forbid", frozen=True)

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
