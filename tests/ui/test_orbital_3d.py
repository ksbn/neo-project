"""The 3D bridge (ui/components/orbital_3d/bridge.py): geometry -> JSON payload -> self-contained HTML."""

import json
import math
import re
from datetime import UTC, date, datetime

import pytest

from planetary_defense.application.enrich_orbit import earth_view_from, sun_view_from
from planetary_defense.domain.models import OrbitLookup, OrbitOrigin
from planetary_defense.domain.units import LUNAR_DISTANCE_KM, km_to_ld
from planetary_defense.ui.components.orbital_3d.bridge import (
    PAYLOAD_VERSION,
    THREE_VERSION,
    build_payload,
    to_html,
)

from fakes import zoozve_orbit

LOOKUP = OrbitLookup(zoozve_orbit(), datetime(2026, 11, 20, tzinfo=UTC), OrbitOrigin.CACHE)
PASS = date(2026, 11, 2)
MISS_LD = km_to_ld(6_019_000)


def payload(*, with_pass: bool = True, name: str = "524522 Zoozve (2002 VE68)") -> dict:
    return build_payload(
        name=name,
        hazardous=True,
        provenance="Orbit from the local cache.",
        trajectory=earth_view_from(LOOKUP, PASS, MISS_LD).trajectory if with_pass else None,
        sun=sun_view_from(LOOKUP, PASS, samples=90),
    )


def test_earth_view_payload_keeps_nasas_distance_and_the_whole_path():
    p = payload()
    earth = p["earth"]
    assert p["version"] == PAYLOAD_VERSION and p["default_mode"] == "earth"
    assert earth["units"] == "LD" and earth["approach_date"] == "2026-11-02"
    assert math.dist(earth["closest"], (0, 0, 0)) == pytest.approx(MISS_LD, abs=1e-3)
    assert earth["miss_ld"] == pytest.approx(15.65, abs=0.01)
    assert earth["model_miss_ld"] == pytest.approx(15.67, abs=0.05)
    assert len(earth["path"]) == 145 and 0 <= earth["closest_index"] < 145
    assert earth["window_days"] == 3
    assert earth["earth_radius_ld"] == pytest.approx(6371 / LUNAR_DISTANCE_KM, abs=1e-6)  # rounded to 6 places


def test_sun_view_payload_has_both_orbits_and_positions_on_the_pass_date():
    sun = payload()["sun"]
    assert sun["units"] == "AU" and sun["date"] == "2026-11-02" and sun["orbit_class"] == "ATE"
    assert len(sun["asteroid_path"]) == len(sun["earth_path"]) == 91
    assert 0.98 < math.dist(sun["earth"], (0, 0, 0)) < 1.02
    # On the pass date the asteroid is ~15.65 LD from Earth: close together in AU terms.
    assert math.dist(sun["asteroid"], sun["earth"]) == pytest.approx(MISS_LD / 389, abs=0.002)


def test_without_a_recorded_pass_only_the_sun_view_is_offered():
    p = payload(with_pass=False)
    assert p["earth"] is None and p["default_mode"] == "sun"


def test_numbers_are_rounded_to_keep_the_page_small():
    text = json.dumps(payload())
    assert not re.search(r"\.\d{7,}", text)  # at most 6 decimals
    assert len(text) < 30_000


# --------------------------------------------------------------------------- #
# HTML
# --------------------------------------------------------------------------- #


def embedded_payload(html: str) -> dict:
    match = re.search(r'<script type="application/json" id="payload">(.*?)</script>', html, re.S)
    assert match, "payload script tag missing"
    return json.loads(match.group(1))


def test_html_embeds_the_payload_and_the_scene():
    p = payload()
    html = to_html(p)
    assert embedded_payload(html) == json.loads(json.dumps(p))
    assert "function buildEarthView" in html and "function buildSunView" in html


def test_three_js_is_pinned_to_an_exact_version():
    html = to_html(payload())
    assert THREE_VERSION.count(".") == 2
    assert f"three@{THREE_VERSION}/build/three.module.min.js" in html
    assert f"three@{THREE_VERSION}/examples/jsm/" in html
    assert "three@latest" not in html


def test_names_from_nasa_cannot_break_out_of_the_payload():
    hostile = '</script><script>alert("x")</script>'
    html = to_html(payload(name=hostile))
    assert hostile not in html
    assert html.count("</script>") == html.count("<script")  # no extra tag was closed or opened
    assert embedded_payload(html)["name"] == hostile  # yet the name survives intact


def test_the_scene_script_never_closes_its_own_tag():
    from planetary_defense.ui.components.orbital_3d.bridge import SCENE_JS

    assert "</script" not in SCENE_JS.read_text().lower()


# --------------------------------------------------------------------------- #
# The inspector's payload choice
# --------------------------------------------------------------------------- #


def test_inspector_payload_uses_the_latest_pass_or_today():
    from planetary_defense.domain.models import AsteroidSummary
    from planetary_defense.ui.components.orbit_stage import viewport_payload

    row = AsteroidSummary("2524522", "Zoozve", 0.2, 0.45, True, PASS, 6_019_000.0, 9.5)
    with_pass = viewport_payload(LOOKUP, "Zoozve", row, "cached", today=date(2026, 11, 22))
    assert with_pass is not None
    assert with_pass["earth"]["approach_date"] == "2026-11-02" and with_pass["sun"]["date"] == "2026-11-02"

    only_alerted = viewport_payload(LOOKUP, "Zoozve", None, "cached", today=date(2026, 11, 22))
    assert only_alerted is not None
    assert only_alerted["earth"] is None and only_alerted["sun"]["date"] == "2026-11-22"
    assert only_alerted["hazardous"] is False



def test_sparkline_scales_values_into_the_box():
    from planetary_defense.ui.components.kpi_strip import sparkline_svg

    svg = sparkline_svg([1, 5, 3], width=100, height=20)
    points = [tuple(map(float, p.split(","))) for p in svg.split('<polyline points="')[1].split('"')[0].split()]
    assert [x for x, _ in points] == [0.0, 50.0, 100.0]
    assert min(y for _, y in points) == 3.0 and max(y for _, y in points) == 17.0  # 3 px margins
    assert "<line" in sparkline_svg([4])  # one point: a flat line, no division by zero


def test_context_arcs_use_only_cached_orbits_of_other_asteroids():
    from planetary_defense.ui.components.orbit_stage import context_arcs, context_candidates

    from fakes import control_room_view

    view = control_room_view()
    candidates = context_candidates(view, selected="2524522")
    assert "2524522" not in candidates and candidates[:2] == ["b", "a"]  # alert asteroids first
    arcs = context_arcs(view, "2524522", {"a": LOOKUP})  # only 'a' has a cached orbit
    assert [name for name, _, _ in arcs] == ["(2001 KF54)"]



def test_approximate_payload_without_an_orbit():
    from planetary_defense.domain.orbital_math import straight_line_flyby

    p = build_payload(name="x", hazardous=False, provenance="NASA rate limit", sun=None, approximate=True,
                      trajectory=straight_line_flyby(PASS, 3.0, 12.0))
    assert p["approximate"] is True and p["sun"] is None and p["default_mode"] == "earth"
    assert p["earth"]["miss_ld"] == 3.0


def test_payload_needs_something_to_draw():
    with pytest.raises(ValueError):
        build_payload(name="x", hazardous=False, provenance="", trajectory=None, sun=None)



def test_stage_falls_back_to_an_approximate_pass_without_an_orbit():
    from planetary_defense.domain.models import AsteroidSummary
    from planetary_defense.ui.components.orbit_stage import viewport_payload

    row = AsteroidSummary("1", "(x)", 0.1, 0.2, False, PASS, 2_000_000.0, 14.0)
    p = viewport_payload(None, "(x)", row, "rate limited", today=date(2026, 11, 22))
    assert p is not None and p["approximate"] is True and p["sun"] is None
    assert p["earth"]["miss_ld"] == pytest.approx(2_000_000 / LUNAR_DISTANCE_KM, abs=1e-3)

    no_pass = AsteroidSummary("1", "(x)", 0.1, 0.2, False, None, None, None)
    assert viewport_payload(None, "(x)", no_pass, "rate limited", today=date(2026, 11, 22)) is None
