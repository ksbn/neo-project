"""The 3D viewport's geometry document, shared by the Streamlit Control Room and the REST API.

:func:`build_payload` turns an Earth-view trajectory and a :class:`SunView` into a small, versioned
JSON document (J2000 ecliptic frame: lunar distances for the Earth view, AU for the Sun view). It is
what ``ui/components/orbital_3d/frontend/scene.js`` draws and what ``GET /objects/{id}/trajectory``
returns (``openapi.yaml``, ADR 0003). :func:`pass_payload` picks what to draw for one object: its
pass from the orbit, or an approximate straight-line pass at NASA's miss distance and speed when
there is no orbit.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Final

from planetary_defense.application.enrich_orbit import SunView, earth_view_from, sun_view_from
from planetary_defense.domain.models import ApproachSummary, OrbitLookup, OrbitOrigin, Trajectory3D, Vec3
from planetary_defense.domain.orbital_math import date_from_jd, straight_line_flyby
from planetary_defense.domain.units import LUNAR_DISTANCE_KM, km_to_ld

PAYLOAD_VERSION: Final = 1
EARTH_RADIUS_KM: Final = 6371.0


def _point(v: Vec3, digits: int) -> list[float]:
    return [round(v.x, digits), round(v.y, digits), round(v.z, digits)]


def _earth_section(t: Trajectory3D) -> dict[str, Any]:
    return {
        "units": "LD",
        "path": [_point(p, 4) for p in t.points_ld],
        "closest": _point(t.closest_point_ld, 4),
        "closest_index": t.closest_index,
        "miss_ld": round(t.miss_distance_ld, 4),
        "model_miss_ld": round(t.model_miss_ld, 4),
        "approach_date": date_from_jd(t.closest_time_jd).isoformat(),
        "window_days": round((t.times_jd[-1] - t.times_jd[0]) / 2),
        "earth_radius_ld": round(EARTH_RADIUS_KM / LUNAR_DISTANCE_KM, 6),
    }


def _sun_section(view: SunView) -> dict[str, Any]:
    return {
        "units": "AU",
        "asteroid_path": [_point(p, 5) for p in view.asteroid_path],
        "earth_path": [_point(p, 5) for p in view.earth_path],
        "asteroid": _point(view.asteroid_position, 6),
        "earth": _point(view.earth_position, 6),
        "date": date_from_jd(view.on_jd).isoformat(),
        "orbit_class": view.lookup.orbit.orbit_class,
    }


def build_payload(
    *,
    name: str,
    hazardous: bool,
    provenance: str,
    trajectory: Trajectory3D | None,
    sun: SunView | None,
    approximate: bool = False,
    context: list[tuple[str, bool, Trajectory3D]] | None = None,
) -> dict[str, Any]:
    """Everything the scene draws.

    ``trajectory`` is None when the asteroid has no recorded pass; ``sun`` is None when there is
    no orbit. ``approximate`` marks a trajectory that isn't from an orbit (a straight-line pass).
    ``context`` adds other asteroids' approach arcs (name, hazardous, trajectory) to the Earth view.
    """
    if trajectory is None and sun is None:
        raise ValueError("nothing to draw: need a trajectory, a Sun view, or both")
    return {
        "version": PAYLOAD_VERSION,
        "name": name,
        "hazardous": hazardous,
        "provenance": provenance,
        "approximate": approximate,
        "default_mode": "earth" if trajectory is not None else "sun",
        "earth": _earth_section(trajectory) if trajectory is not None else None,
        "sun": _sun_section(sun) if sun is not None else None,
        "context": [
            {"name": name, "hazardous": hazardous, "path": [_point(p, 3) for p in t.points_ld[::2]],
             "closest": _point(t.closest_point_ld, 3), "miss_ld": round(t.miss_distance_ld, 3)}
            for name, hazardous, t in (context or [])
        ],
    }


def pass_payload(
    lookup: OrbitLookup | None,
    *,
    name: str,
    hazardous: bool,
    approach: ApproachSummary | None,
    provenance: str,
    today: date,
    context: list[tuple[str, bool, Trajectory3D]] | None = None,
) -> dict[str, Any] | None:
    """What the viewport draws for one object, or None when there is nothing to draw.

    With an orbit: the Earth view of ``approach`` (if any) and the Sun view on its date (else today).
    Without one: an approximate straight-line pass from NASA's miss distance and speed.
    """
    if lookup is not None:
        on = approach.close_approach_date if approach is not None else today
        trajectory = (earth_view_from(lookup, on, km_to_ld(approach.miss_distance_km)).trajectory
                      if approach is not None else None)
        payload = build_payload(name=name, hazardous=hazardous, provenance=provenance, trajectory=trajectory,
                                sun=sun_view_from(lookup, on), context=context)
        if payload["earth"] is not None and approach is not None:
            # Label NASA's date: for passes years away the two-body model's closest moment drifts by days.
            payload["earth"]["approach_date"] = approach.close_approach_date.isoformat()
        return payload
    if approach is not None:
        trajectory = straight_line_flyby(approach.close_approach_date, km_to_ld(approach.miss_distance_km),
                                         approach.relative_velocity_km_s)
        return build_payload(name=name, hazardous=hazardous, provenance=provenance, trajectory=trajectory,
                             sun=None, approximate=True, context=context)
    return None


def provenance(lookup: OrbitLookup | None, reason: str | None = None) -> str:
    """Where the geometry comes from, in one sentence, for the viewport's legend."""
    if lookup is None:
        why = f" ({reason})" if reason else ""
        return f"No orbit available{why}: approximate straight-line pass at NASA's miss distance and speed."
    fetched = lookup.fetched_at.astimezone().strftime("%Y-%m-%d %H:%M")
    text = {
        OrbitOrigin.NASA: "Orbit fetched from NASA just now.",
        OrbitOrigin.CACHE: f"Orbit from the local cache, fetched from NASA {fetched}.",
        OrbitOrigin.STALE_CACHE: f"Stale orbit from the local cache ({fetched}).",
    }[lookup.origin]
    return f"{text} {lookup.note}" if lookup.note else text


__all__ = ["EARTH_RADIUS_KM", "PAYLOAD_VERSION", "build_payload", "pass_payload", "provenance"]
