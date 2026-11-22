"""Pydantic models for the bodies in ``openapi.yaml`` (the contract is the source of truth).

The request model is strict so that it accepts exactly what the contract's JSON Schema
accepts: ``"20"`` is not a number, ``true`` is not a number, unknown fields are rejected.
``tests/api/test_contract.py`` checks both directions against ``openapi.yaml``.
Every nullable field is required in responses and serialised as ``null``, never left out.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from planetary_defense.domain.catalog import ObjectDetail, ObjectRow, Summary, WatchlistEntry
from planetary_defense.domain.models import AnomalyAlert, ApproachSummary, Severity
from planetary_defense.domain.units import au_to_ld, km_to_ld

NeoId = Annotated[str, StringConstraints(pattern=r"^[0-9]{1,20}$")]
SeverityName = Literal["info", "watch", "warning", "critical"]
PriorityName = Literal["low", "medium", "high", "critical"]
Tag = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,23}$")]


def severity_name(s: Severity) -> SeverityName:
    return s.name.lower()  # type: ignore[return-value]  # the four names are exactly SeverityName


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- #
# Request
# --------------------------------------------------------------------------- #


class WatchlistEntryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    neo_reference_id: NeoId
    priority: PriorityName
    alert_threshold_ld: Annotated[float, Field(gt=0, le=100)] | None = None
    note: Annotated[str, Field(max_length=500)] | None = None
    tags: Annotated[list[Tag], Field(max_length=5)] = Field(default_factory=list)

    @field_validator("tags")
    @classmethod
    def _unique(cls, tags: list[str]) -> list[str]:
        if len(set(tags)) != len(tags):
            raise ValueError("tags must be unique")
        return tags


# --------------------------------------------------------------------------- #
# Responses
# --------------------------------------------------------------------------- #


class Approach(_Out):
    date: date
    orbiting_body: str
    miss_distance_km: float
    miss_distance_ld: float
    relative_velocity_km_s: float

    @classmethod
    def of(cls, a: ApproachSummary) -> Approach:
        return cls(date=a.close_approach_date, orbiting_body=a.orbiting_body, miss_distance_km=a.miss_distance_km,
                   miss_distance_ld=km_to_ld(a.miss_distance_km), relative_velocity_km_s=a.relative_velocity_km_s)


def _m(km: float | None) -> float | None:
    return km * 1000 if km is not None else None


class ObjectSummary(_Out):
    neo_reference_id: str
    name: str
    is_potentially_hazardous: bool
    is_sentry_object: bool
    absolute_magnitude_h: float | None
    estimated_diameter_min_m: float | None
    estimated_diameter_max_m: float | None
    latest_approach: Approach | None
    alert_level: SeverityName | None
    watched: bool

    @classmethod
    def fields_of(cls, r: ObjectRow) -> dict:
        return dict(
            neo_reference_id=r.neo_reference_id, name=r.name, is_potentially_hazardous=r.is_potentially_hazardous,
            is_sentry_object=r.is_sentry_object, absolute_magnitude_h=r.absolute_magnitude_h,
            estimated_diameter_min_m=_m(r.estimated_diameter_min_km),
            estimated_diameter_max_m=_m(r.estimated_diameter_max_km),
            latest_approach=Approach.of(r.latest_approach) if r.latest_approach else None,
            alert_level=severity_name(r.alert_level) if r.alert_level is not None else None,
            watched=r.watched,
        )

    @classmethod
    def of(cls, r: ObjectRow) -> ObjectSummary:
        return cls(**cls.fields_of(r))


class Pagination(_Out):
    total: int
    limit: int
    offset: int
    next_offset: int | None
    prev_offset: int | None

    @classmethod
    def of(cls, total: int, limit: int, offset: int) -> Pagination:
        return cls(total=total, limit=limit, offset=offset,
                   next_offset=offset + limit if offset + limit < total else None,
                   prev_offset=max(offset - limit, 0) if offset > 0 else None)


class ObjectPage(_Out):
    items: list[ObjectSummary]
    pagination: Pagination


class Orbit(_Out):
    tracked: bool
    fetched_at: datetime | None
    orbit_class: str | None
    semi_major_axis_au: float | None
    eccentricity: float | None
    inclination_deg: float | None
    ascending_node_deg: float | None
    perihelion_argument_deg: float | None
    mean_anomaly_deg: float | None
    epoch_jd: float | None
    moid_au: float | None
    moid_ld: float | None

    @classmethod
    def untracked(cls) -> Orbit:
        return cls(tracked=False, fetched_at=None, orbit_class=None, semi_major_axis_au=None, eccentricity=None,
                   inclination_deg=None, ascending_node_deg=None, perihelion_argument_deg=None, mean_anomaly_deg=None,
                   epoch_jd=None, moid_au=None, moid_ld=None)


class Alert(_Out):
    rule: str
    severity: SeverityName
    approach_date: date
    message: str
    value: float | None
    threshold: float | None

    @classmethod
    def of(cls, a: AnomalyAlert) -> Alert:
        if a.close_approach_date is None:  # stored alerts always have a date (NOT NULL column)
            raise ValueError("a stored alert has no approach date")
        return cls(rule=a.rule, severity=severity_name(a.severity), approach_date=a.close_approach_date,
                   message=a.message, value=a.value, threshold=a.threshold)


class WatchlistEntryOut(_Out):
    id: str
    neo_reference_id: str
    name: str
    priority: PriorityName
    alert_threshold_ld: float | None
    note: str | None
    tags: list[str]
    created_at: datetime

    @classmethod
    def of(cls, e: WatchlistEntry) -> WatchlistEntryOut:
        return cls(id=e.id, neo_reference_id=e.neo_reference_id, name=e.name, priority=e.priority.value,
                   alert_threshold_ld=e.alert_threshold_ld, note=e.note, tags=list(e.tags),
                   created_at=e.created_at.astimezone(UTC))


class ObjectDetailOut(ObjectSummary):
    nasa_jpl_url: str | None
    orbit: Orbit
    next_predicted_approach: Approach | None
    approach_history: list[Approach]
    alerts: list[Alert]
    watch: WatchlistEntryOut | None

    @classmethod
    def of_detail(cls, d: ObjectDetail, today: date) -> ObjectDetailOut:
        orbit = Orbit.untracked()
        if d.orbit is not None:
            o, el = d.orbit.orbit, d.orbit.orbit.elements
            orbit = Orbit(
                tracked=True, fetched_at=d.orbit.fetched_at.astimezone(UTC), orbit_class=o.orbit_class,
                semi_major_axis_au=el.semi_major_axis_au, eccentricity=el.eccentricity,
                inclination_deg=el.inclination_deg, ascending_node_deg=el.ascending_node_deg,
                perihelion_argument_deg=el.perihelion_argument_deg, mean_anomaly_deg=el.mean_anomaly_deg,
                epoch_jd=el.epoch_jd, moid_au=o.moid_au, moid_ld=au_to_ld(o.moid_au) if o.moid_au is not None else None,
            )
        upcoming = d.next_predicted_approach(today)
        return cls(
            **ObjectSummary.fields_of(d.row),
            nasa_jpl_url=d.nasa_jpl_url,
            orbit=orbit,
            next_predicted_approach=Approach.of(upcoming) if upcoming else None,
            approach_history=[Approach.of(a) for a in d.approach_history],
            alerts=[Alert.of(a) for a in d.alerts],
            watch=WatchlistEntryOut.of(d.watch) if d.watch else None,
        )


class WatchlistPageOut(_Out):
    items: list[WatchlistEntryOut]
    pagination: Pagination


# --------------------------------------------------------------------------- #
# Summary (ADR 0001)
# --------------------------------------------------------------------------- #


class AlertCounts(_Out):
    info: int
    watch: int
    warning: int
    critical: int


class DailyCount(_Out):
    date: date
    count: int


class ClosestApproach(_Out):
    neo_reference_id: str
    name: str
    approach: Approach


class SummaryOut(_Out):
    total_objects: int
    hazardous_objects: int
    watched_objects: int
    alert_counts: AlertCounts
    closest_approach: ClosestApproach | None
    latest_approach_date: date | None
    last_sync_at: datetime | None
    approaches_per_day: list[DailyCount]

    @classmethod
    def of(cls, s: Summary) -> SummaryOut:
        closest = (ClosestApproach(neo_reference_id=s.closest[0], name=s.closest[1], approach=Approach.of(s.closest[2]))
                   if s.closest else None)
        return cls(
            total_objects=s.total_objects, hazardous_objects=s.hazardous_objects, watched_objects=s.watched_objects,
            alert_counts=AlertCounts(**{severity_name(k): v for k, v in s.alert_counts.items()}),
            closest_approach=closest, latest_approach_date=s.latest_approach_date,
            last_sync_at=s.last_sync_at.astimezone(UTC) if s.last_sync_at else None,
            approaches_per_day=[DailyCount(date=d, count=n) for d, n in s.approaches_per_day],
        )


# --------------------------------------------------------------------------- #
# Trajectory (ADR 0003)
# --------------------------------------------------------------------------- #

Vec3 = Annotated[list[float], Field(min_length=3, max_length=3)]


class EarthViewOut(_Out):
    units: Literal["LD"]
    path: list[Vec3]
    closest: Vec3
    closest_index: int
    miss_ld: float
    model_miss_ld: float
    approach_date: date
    window_days: int
    earth_radius_ld: float


class SunViewOut(_Out):
    units: Literal["AU"]
    asteroid_path: list[Vec3]
    earth_path: list[Vec3]
    asteroid: Vec3
    earth: Vec3
    date: date
    orbit_class: str | None


class TrajectoryOut(_Out):
    version: Literal[1]
    neo_reference_id: str
    name: str
    hazardous: bool
    # "pass" is a Python keyword: the field is pass_ in code and "pass" on the wire.
    pass_: Literal["latest", "next"] = Field(alias="pass")
    approximate: bool
    orbit_origin: Literal["cache", "nasa", "stale_cache"] | None
    provenance: str
    default_mode: Literal["earth", "sun"] | None
    earth: EarthViewOut | None
    sun: SunViewOut | None

    @classmethod
    def of(cls, neo_reference_id: str, name: str, hazardous: bool, which: str, payload: dict | None,
           orbit_origin: str | None, provenance: str) -> TrajectoryOut:
        p = payload or {}
        return cls.model_validate({
            "version": 1, "neo_reference_id": neo_reference_id, "name": name, "hazardous": hazardous,
            "pass": which, "approximate": bool(p.get("approximate", False)), "orbit_origin": orbit_origin,
            "provenance": provenance, "default_mode": p.get("default_mode"),
            "earth": p.get("earth"), "sun": p.get("sun"),
        })


ErrorCode = Literal["BAD_REQUEST", "NOT_FOUND", "ALREADY_WATCHED", "VALIDATION_FAILED", "UNKNOWN_OBJECT",
                    "IDEMPOTENCY_KEY_REUSED", "RATE_LIMITED", "INTERNAL_ERROR", "STORAGE_UNAVAILABLE"]


class ErrorDetail(_Out):
    field: str | None
    reason: str


class ErrorResponse(_Out):
    code: ErrorCode
    message: str
    details: list[ErrorDetail]
    request_id: str
    timestamp: datetime
