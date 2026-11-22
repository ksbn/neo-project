"""GET /objects and GET /objects/{id}: filters, pagination, sorting, detail and nullable fields.

Every response is also validated against openapi.yaml by the ContractClient.
"""

from __future__ import annotations

import pytest

from planetary_defense.domain.units import LUNAR_DISTANCE_KM

from .conftest import BARE, FAST, KF54, NEAR, ZOOZVE

OBJ = "/objects/{neo_reference_id}"


def ids(response) -> list[str]:
    return [o["neo_reference_id"] for o in response.json()["items"]]


def test_default_listing_is_newest_pass_first_with_pagination_metadata(api):
    r = api.get("/objects")
    assert r.status_code == 200
    assert ids(r) == [FAST, NEAR, BARE, KF54, ZOOZVE]
    assert r.json()["pagination"] == {"total": 5, "limit": 25, "offset": 0, "next_offset": None, "prev_offset": None}


def test_summary_fields_in_display_units_with_the_worst_alert(api):
    near = next(o for o in api.get("/objects").json()["items"] if o["neo_reference_id"] == NEAR)
    assert near["alert_level"] == "critical"  # 0.8 LD: inside the Moon's orbit
    assert near["latest_approach"]["miss_distance_ld"] == pytest.approx(0.8, abs=1e-6)
    assert near["latest_approach"]["miss_distance_km"] == pytest.approx(0.8 * LUNAR_DISTANCE_KM, abs=1e-3)
    assert near["estimated_diameter_max_m"] == pytest.approx(20.0)


def test_unknown_values_are_null_never_zero(api):
    bare = next(o for o in api.get("/objects").json()["items"] if o["neo_reference_id"] == BARE)
    assert bare["absolute_magnitude_h"] is None
    assert bare["estimated_diameter_min_m"] is None and bare["estimated_diameter_max_m"] is None
    assert bare["alert_level"] is None


@pytest.mark.parametrize(("params", "expected"), [
    ({"hazardous": "true"}, {ZOOZVE, KF54}),
    ({"hazardous": "false"}, {NEAR, FAST, BARE}),
    ({"max_distance_ld": 16}, {NEAR, ZOOZVE}),
    ({"min_velocity_km_s": 20}, {NEAR, FAST}),
    ({"min_severity": "warning"}, {NEAR, ZOOZVE, KF54}),
    ({"min_severity": "critical"}, {NEAR}),
    ({"approach_from": "2026-11-06", "approach_to": "2026-11-20"}, {KF54, BARE, NEAR}),
    ({"q": "zOoZ"}, {ZOOZVE}),
    ({"q": "%"}, set()),  # a literal character, not a wildcard
    ({"hazardous": "true", "max_distance_ld": 16}, {ZOOZVE}),  # filters combine with AND
])
def test_filters(api, params, expected):
    r = api.get("/objects", params=params)
    assert set(ids(r)) == expected and r.json()["pagination"]["total"] == len(expected)


@pytest.mark.parametrize(("sort", "expected"), [
    ("approach_date", [ZOOZVE, KF54, BARE, NEAR, FAST]),
    ("distance", [NEAR, ZOOZVE, KF54, FAST, BARE]),
    ("-distance", [BARE, FAST, KF54, ZOOZVE, NEAR]),
    ("-velocity", [FAST, NEAR, KF54, ZOOZVE, BARE]),
    ("-diameter", [KF54, ZOOZVE, FAST, NEAR, BARE]),  # unknown size last
    ("name", [KF54, BARE, FAST, NEAR, ZOOZVE]),  # (2001 KF54) < (2026 BARE) < ...
])
def test_sorting(api, sort, expected):
    assert ids(api.get("/objects", params={"sort": sort})) == expected


def test_pages_are_stable_and_cover_everything_once(api):
    first = api.get("/objects", params={"limit": 2, "sort": "distance"})
    second = api.get("/objects", params={"limit": 2, "offset": 2, "sort": "distance"})
    last = api.get("/objects", params={"limit": 2, "offset": 4, "sort": "distance"})
    assert first.json()["pagination"] == {"total": 5, "limit": 2, "offset": 0, "next_offset": 2, "prev_offset": None}
    assert last.json()["pagination"]["next_offset"] is None and last.json()["pagination"]["prev_offset"] == 2
    assert ids(first) + ids(second) + ids(last) == [NEAR, ZOOZVE, KF54, FAST, BARE]


def test_a_page_past_the_end_is_empty_but_keeps_the_total(api):
    r = api.get("/objects", params={"offset": 50})
    assert r.json()["items"] == [] and r.json()["pagination"]["total"] == 5


# --------------------------------------------------------------------------- #
# Detail
# --------------------------------------------------------------------------- #


def test_detail_has_every_attribute_including_the_cached_orbit(api):
    d = api.get(OBJ, path_params={"neo_reference_id": ZOOZVE}).json()
    assert d["name"] == "524522 Zoozve (2002 VE68)" and d["is_potentially_hazardous"] is True
    assert d["orbit"]["tracked"] is True and d["orbit"]["orbit_class"] == "ATE"
    assert d["orbit"]["inclination_deg"] == pytest.approx(9.0373, abs=1e-4)
    assert d["orbit"]["moid_ld"] == pytest.approx(0.025508 * 389, abs=1e-6)
    assert d["next_predicted_approach"]["date"] == "2034-10-31"  # from NASA's approach list, after TODAY
    assert [a["date"] for a in d["approach_history"]] == ["2026-11-02"]
    assert [(a["rule"], a["severity"]) for a in d["alerts"]] == [("hazardous_close_pass", "warning"),
                                                                 ("potentially_hazardous", "info")]
    assert d["watch"] is None
    assert d["nasa_jpl_url"].endswith(ZOOZVE)


def test_an_untracked_orbit_is_all_null_not_zero(api):
    d = api.get(OBJ, path_params={"neo_reference_id": KF54}).json()
    orbit = d.pop("orbit")
    assert orbit["tracked"] is False
    assert all(v is None for k, v in orbit.items() if k != "tracked")
    assert d["next_predicted_approach"] is None


def test_an_unknown_object_is_404(api):
    r = api.get(OBJ, path_params={"neo_reference_id": "9999999"})
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"
    assert "9999999" in r.json()["message"]
