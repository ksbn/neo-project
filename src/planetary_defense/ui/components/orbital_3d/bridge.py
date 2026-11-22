"""Python-to-browser bridge for the 3D viewport.

:func:`build_payload` (from ``application/viewport.py``, shared with the REST API) builds the
versioned JSON document the scene draws (J2000 ecliptic frame: LD for the Earth view, AU for the
Sun view). :func:`to_html` embeds it, with the
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

# The geometry document lives in the application layer (shared with the REST API); re-exported here.
from planetary_defense.application.viewport import EARTH_RADIUS_KM, PAYLOAD_VERSION, build_payload

FRONTEND: Final = Path(__file__).parent / "frontend"
INDEX_HTML: Final = FRONTEND / "index.html"
SCENE_JS: Final = FRONTEND / "scene.js"

#: three.js from cdn.jsdelivr.net, pinned to an exact release; textures from the same release's repository.
THREE_VERSION: Final = "0.170.0"
TEXTURE_BASE: Final = "https://cdn.jsdelivr.net/gh/mrdoob/three.js@r170/examples/textures/planets/"
HEIGHT: Final = 520


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


__all__ = ["EARTH_RADIUS_KM", "HEIGHT", "PAYLOAD_VERSION", "THREE_VERSION", "build_payload", "render", "to_html"]
