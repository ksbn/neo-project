"""GET /objects/{id}/trajectory (ADR 0003): the 3D viewport's geometry, cache-first, NASA on a miss."""

from __future__ import annotations

import math
from contextlib import nullcontext

import pytest
from fastapi.testclient import TestClient

from planetary_defense.api.app import create_app
from planetary_defense.api.errors import RateLimiter
from planetary_defense.domain.errors import UpstreamUnavailable

from .conftest import KF54, TODAY, ZOOZVE
from contract import ContractClient
from fakes import FakeOrbitSource

PATH = "/objects/{neo_reference_id}/trajectory"


def client_for(conn, source=None) -> TestClient:
    return TestClient(create_app(lambda: nullcontext(conn), today=lambda: TODAY,
                                 read_limiter=RateLimiter(1000, 100), write_limiter=RateLimiter(1000, 100),
                                 open_orbit_source=(lambda: nullcontext(source)) if source else None))


def get(conn, ref: str, source=None, **params):
    with client_for(conn, source) as client:
        return ContractClient(client).get(PATH, path_params={"neo_reference_id": ref}, params=params)


def test_latest_pass_from_the_cached_orbit(seeded):
    t = get(seeded, ZOOZVE).json()
    assert (t["pass"], t["approximate"], t["orbit_origin"], t["default_mode"]) == ("latest", False, "cache", "earth")
    earth, sun = t["earth"], t["sun"]
    assert earth["approach_date"] == "2026-11-02" and earth["miss_ld"] == pytest.approx(15.65, abs=0.01)
    assert math.dist(earth["closest"], (0, 0, 0)) == pytest.approx(earth["miss_ld"], abs=1e-3)  # placed at NASA's miss
    assert earth["model_miss_ld"] == pytest.approx(15.67, abs=0.05)  # what the orbit model alone gives
    assert sun["date"] == "2026-11-02" and sun["orbit_class"] == "ATE" and len(sun["asteroid_path"]) > 100
    assert "local cache" in t["provenance"] and t["name"] == "524522 Zoozve (2002 VE68)" and t["hazardous"]


def test_the_next_predicted_pass_in_3d(seeded):
    t = get(seeded, ZOOZVE, **{"pass": "next"}).json()
    assert t["pass"] == "next" and t["earth"]["approach_date"] == "2034-10-31"
    assert t["earth"]["miss_ld"] == pytest.approx(16.80, abs=0.01) and t["sun"]["date"] == "2034-10-31"
    # Eight years out, the two-body model drifts from NASA's n-body prediction; the payload says so.
    assert t["earth"]["model_miss_ld"] == pytest.approx(16.28, abs=0.05)


def test_without_an_orbit_the_pass_is_an_honest_straight_line(seeded):
    t = get(seeded, KF54).json()  # no orbit cached, no NASA configured
    assert t["approximate"] is True and t["orbit_origin"] is None and t["sun"] is None
    assert t["earth"]["miss_ld"] == pytest.approx(18.84, abs=0.01)
    assert "not in the local cache" in t["provenance"]


def test_a_next_pass_needs_an_orbit_so_there_is_nothing_to_draw(seeded):
    t = get(seeded, KF54, **{"pass": "next"}).json()
    assert (t["default_mode"], t["earth"], t["sun"]) == (None, None, None)


def test_a_cache_miss_is_fetched_from_nasa_once_and_cached(seeded):
    source = FakeOrbitSource()
    first = get(seeded, KF54, source).json()
    second = get(seeded, KF54, source).json()
    assert source.calls == [KF54]
    assert (first["orbit_origin"], second["orbit_origin"]) == ("nasa", "cache")
    assert first["approximate"] is False and first["sun"] is not None


def test_nasa_failing_falls_back_to_the_straight_line(seeded):
    t = get(seeded, KF54, FakeOrbitSource(fail=UpstreamUnavailable("NASA down"))).json()
    assert t["approximate"] is True and t["orbit_origin"] is None
    assert "could not be reached" in t["provenance"]


def test_unknown_object_and_bad_parameters(seeded):
    assert get(seeded, "9999999").status_code == 404
    r = get(seeded, ZOOZVE, **{"pass": "previous"})
    assert r.status_code == 400 and r.json()["details"][0]["field"] == "query.pass"
    assert get(seeded, "abc").status_code == 400
