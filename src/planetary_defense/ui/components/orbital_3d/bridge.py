"""Python-to-browser bridge for the 3D viewport.

:func:`build_payload` turns the application's :class:`EarthView` / :class:`SunView`
into a small, versioned JSON document (positions in the J2000 ecliptic frame:
LD for the Earth view, AU for the Sun view). :func:`to_html` embeds it, with the
scene script, into ``frontend/index.html``; :func:`render` shows that page in an
iframe. The browser only draws: every number is computed in Python.

The payload is embedded as JSON inside a ``<script>`` tag, so ``<``, ``>``, ``&``
and the JavaScript line separators are escaped: an asteroid name from NASA can
never close the tag or inject markup.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from planetary_defense.application.enrich_orbit import SunView
from planetary_defense.domain.models import Trajectory3D, Vec3
from planetary_defense.domain.orbital_math import date_from_jd
from planetary_defense.domain.units import LUNAR_DISTANCE_KM

FRONTEND: Final = Path(__file__).parent / "frontend"
INDEX_HTML: Final = FRONTEND / "index.html"
SCENE_JS: Final = FRONTEND / "scene.js"

#: three.js from cdn.jsdelivr.net, pinned to an exact release; textures from the same release's repository.
THREE_VERSION: Final = "0.170.0"
TEXTURE_BASE: Final = "https://cdn.jsdelivr.net/gh/mrdoob/three.js@r170/examples/textures/planets/"
PAYLOAD_VERSION: Final = 1
HEIGHT: Final = 520
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


def _json_for_script_tag(obj: Any) -> str:
    text = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    for raw, escaped in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"), (" ", "\\u2028"),
                         (" ", "\\u2029")):
        text = text.replace(raw, escaped)
    return text


def to_html(payload: dict[str, Any]) -> str:
    """A self-contained page: the template, three.js import map, scene script and payload."""
    page = INDEX_HTML.read_text(encoding="utf-8")
    scene = SCENE_JS.read_text("utf-8").replace("__TEXTURE_BASE__", TEXTURE_BASE)
    page = page.replace("__THREE_VERSION__", THREE_VERSION).replace("__SCENE_JS__", scene)
    return page.replace("__PAYLOAD__", _json_for_script_tag(payload))  # last: the payload is never re-scanned


def render(payload: dict[str, Any], height: int = HEIGHT) -> None:
    """Show the viewport in the current Streamlit container.

    ``st.iframe`` runs the page's JavaScript, so it must only ever get this module's own
    template: external text (asteroid names from NASA) reaches it only through the escaped
    payload, and the scene HTML-escapes it again before putting it in the legend.
    """
    import streamlit as st  # only the UI needs Streamlit

    st.iframe(to_html(payload), height=height)
