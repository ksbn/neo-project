"""ADR 0001 (GET /summary) and ADR 0002 (GET /watchlist, the watched flag and filter)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from planetary_defense.domain.models import IngestionRun, RunStatus
from planetary_defense.infrastructure.repository import IngestionRunRepository

from .conftest import BARE, FAST, KF54, NEAR, ZOOZVE, app_for
from contract import ContractClient


def test_summary_aggregates_every_object(api, seeded):
    IngestionRunRepository(seeded).record(IngestionRun(
        started_at=datetime(2026, 11, 22, 8, 0, tzinfo=UTC), finished_at=datetime(2026, 11, 22, 8, 1, tzinfo=UTC),
        start_date=date(2026, 11, 20), end_date=date(2026, 11, 22), status=RunStatus.SUCCESS))
    api.post("/watchlist", json={"neo_reference_id": KF54, "priority": "medium"})

    s = api.get("/summary").json()
    assert (s["total_objects"], s["hazardous_objects"], s["watched_objects"]) == (5, 2, 1)
    assert s["alert_counts"] == {"info": 0, "watch": 1, "warning": 2, "critical": 1}  # worst alert per object
    assert s["closest_approach"]["neo_reference_id"] == NEAR
    assert s["closest_approach"]["approach"]["miss_distance_ld"] == pytest.approx(0.8, abs=1e-6)
    assert s["latest_approach_date"] == "2026-11-21"
    assert s["last_sync_at"] == "2026-11-22T08:01:00Z"
    days = s["approaches_per_day"]
    assert len(days) == 14 and days[0]["date"] == "2026-11-09" and days[-1]["date"] == "2026-11-22"
    assert {d["date"]: d["count"] for d in days if d["count"]} == {"2026-11-10": 1, "2026-11-20": 1, "2026-11-21": 1}


def test_summary_of_an_empty_database_uses_null_not_fake_values(conn):
    with TestClient(app_for(conn)) as client:
        s = ContractClient(client).get("/summary").json()
    assert s["total_objects"] == 0 and s["closest_approach"] is None
    assert s["latest_approach_date"] is None and s["last_sync_at"] is None
    assert [d["count"] for d in s["approaches_per_day"]] == [0] * 14


def test_watchlist_lists_newest_first_with_pagination(api):
    for ref in (ZOOZVE, FAST, BARE):
        api.post("/watchlist", json={"neo_reference_id": ref, "priority": "low"})
    first = api.get("/watchlist", params={"limit": 2}).json()
    assert [e["neo_reference_id"] for e in first["items"]] == [BARE, FAST]
    assert first["pagination"] == {"total": 3, "limit": 2, "offset": 0, "next_offset": 2, "prev_offset": None}
    rest = api.get("/watchlist", params={"limit": 2, "offset": 2}).json()
    assert [e["neo_reference_id"] for e in rest["items"]] == [ZOOZVE]


def test_an_empty_watchlist(api):
    assert api.get("/watchlist").json() == {"items": [], "pagination": {"total": 0, "limit": 25, "offset": 0,
                                                                        "next_offset": None, "prev_offset": None}}


def test_objects_carry_and_filter_on_watched(api):
    api.post("/watchlist", json={"neo_reference_id": ZOOZVE, "priority": "high"})
    items = {o["neo_reference_id"]: o["watched"] for o in api.get("/objects").json()["items"]}
    assert items == {ZOOZVE: True, KF54: False, NEAR: False, FAST: False, BARE: False}
    watched = api.get("/objects", params={"watched": "true"}).json()
    assert [o["neo_reference_id"] for o in watched["items"]] == [ZOOZVE]
    assert api.get("/objects", params={"watched": "false", "hazardous": "true"}).json()["pagination"]["total"] == 1
    assert api.get("/objects/{neo_reference_id}", path_params={"neo_reference_id": ZOOZVE}).json()["watched"] is True


def test_watchlist_rejects_bad_paging_with_400(api):
    r = api.get("/watchlist", params={"limit": 0})
    assert r.status_code == 400 and r.json()["details"][0]["field"] == "query.limit"

