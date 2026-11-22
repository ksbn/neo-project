"""POST /watchlist: validation (400 vs 422), the business key (409), and idempotent retries."""

from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager

import psycopg
import pytest
from fastapi.testclient import TestClient

from planetary_defense.api.app import create_app
from planetary_defense.api.errors import RateLimiter
from planetary_defense.domain.catalog import Priority, WatchRequest
from planetary_defense.infrastructure.db import connect

from .conftest import BARE, FAST, KF54, NEAR, ZOOZVE, seed
from contract import ContractClient

GOOD = {"neo_reference_id": ZOOZVE, "priority": "high", "alert_threshold_ld": 20,
        "note": "Re-check before the 2034 pass.", "tags": ["aten", "pha"]}


def rows(conn: psycopg.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()
    return row[0] if row else 0


def test_create_returns_201_with_the_entry_and_its_location(api, seeded):
    r = api.post("/watchlist", json=GOOD)
    assert r.status_code == 201
    body = r.json()
    assert {k: body[k] for k in GOOD} == {**GOOD, "alert_threshold_ld": 20.0}
    assert body["name"] == "524522 Zoozve (2002 VE68)"
    uuid.UUID(body["id"])
    assert r.headers["Location"] == f"/api/v1/objects/{ZOOZVE}" and r.headers["Idempotent-Replayed"] == "false"
    watch = api.get("/objects/{neo_reference_id}", path_params={"neo_reference_id": ZOOZVE}).json()["watch"]
    assert watch == body  # the detail view shows it
    assert rows(seeded) == 1


def test_optional_fields_default_to_null_and_empty(api):
    body = api.post("/watchlist", json={"neo_reference_id": KF54, "priority": "low"}).json()
    assert (body["alert_threshold_ld"], body["note"], body["tags"]) == (None, None, [])


def test_watching_the_same_object_twice_is_a_conflict(api, seeded):
    assert api.post("/watchlist", json=GOOD).status_code == 201
    r = api.post("/watchlist", json={**GOOD, "priority": "low"})
    assert r.status_code == 409 and r.json()["code"] == "ALREADY_WATCHED"
    assert r.json()["details"][0]["field"] == "body.neo_reference_id"
    assert rows(seeded) == 1


def test_an_unknown_object_is_422_not_404(api):
    """The URL exists; it's the body that refers to something missing."""
    r = api.post("/watchlist", json={**GOOD, "neo_reference_id": "9999999"})
    assert r.status_code == 422 and r.json()["code"] == "UNKNOWN_OBJECT"


def test_validation_errors_name_every_bad_field(api):
    r = api.post("/watchlist", json={"neo_reference_id": NEAR, "priority": "urgent", "alert_threshold_ld": 500,
                                     "tags": ["OK", "ok"], "colour": "red"})
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_FAILED"
    fields = {d["field"] for d in r.json()["details"]}
    assert {"body.priority", "body.alert_threshold_ld", "body.tags.0", "body.colour"} <= fields


@pytest.mark.parametrize("raw", [b"{not json", b"", b"[]", b'"watch"'])
def test_an_unreadable_body_is_400(api, raw):
    r = api.post("/watchlist", content=raw, headers={"Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["code"] == "BAD_REQUEST"


@pytest.mark.parametrize("key", ["short", "has space in it", "x" * 129])
def test_a_malformed_idempotency_key_is_400(api, key):
    r = api.post("/watchlist", json=GOOD, headers={"Idempotency-Key": key})
    assert r.status_code == 400 and r.json()["details"][0]["field"] == "header.Idempotency-Key"


# --------------------------------------------------------------------------- #
# Idempotency
# --------------------------------------------------------------------------- #


def test_a_retry_with_the_same_key_replays_the_original_201(api, seeded):
    key = {"Idempotency-Key": str(uuid.uuid4())}
    first = api.post("/watchlist", json=GOOD, headers=key)
    retry = api.post("/watchlist", json=GOOD, headers=key)
    assert first.status_code == retry.status_code == 201
    assert retry.json() == first.json()  # same id, same created_at: the original result
    assert (first.headers["Idempotent-Replayed"], retry.headers["Idempotent-Replayed"]) == ("false", "true")
    assert rows(seeded) == 1


def test_the_same_request_in_a_different_json_form_is_still_a_replay(api):
    key = {"Idempotency-Key": "client-retry-0001"}
    api.post("/watchlist", json={"neo_reference_id": FAST, "priority": "low"}, headers=key)
    reordered = b'{ "priority": "low",  "neo_reference_id": "3999999", "tags": [], "note": null }'
    r = api.post("/watchlist", content=reordered, headers={**key, "Content-Type": "application/json"})
    assert r.status_code == 201 and r.headers["Idempotent-Replayed"] == "true"


def test_reusing_a_key_with_a_different_body_is_rejected(api, seeded):
    key = {"Idempotency-Key": "client-retry-0002"}
    api.post("/watchlist", json=GOOD, headers=key)
    r = api.post("/watchlist", json={**GOOD, "priority": "low"}, headers=key)
    assert r.status_code == 422 and r.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert r.json()["details"][0]["field"] == "header.Idempotency-Key"
    assert rows(seeded) == 1


def test_a_failed_request_does_not_burn_its_key(api, seeded):
    key = {"Idempotency-Key": "client-retry-0003"}
    assert api.post("/watchlist", json={**GOOD, "neo_reference_id": "9999999"}, headers=key).status_code == 422
    assert api.post("/watchlist", json={**GOOD, "neo_reference_id": BARE}, headers=key).status_code == 201


def test_a_new_key_for_the_same_object_hits_the_business_key(api):
    assert api.post("/watchlist", json=GOOD, headers={"Idempotency-Key": "first-attempt"}).status_code == 201
    assert api.post("/watchlist", json=GOOD, headers={"Idempotency-Key": "second-attempt"}).status_code == 409


def test_an_expired_key_is_forgotten(api, seeded):
    key = {"Idempotency-Key": "client-retry-0004"}
    api.post("/watchlist", json=GOOD, headers=key)
    seeded.execute("UPDATE idempotency_keys SET expires_at = NOW() - INTERVAL '1 second'")
    r = api.post("/watchlist", json=GOOD, headers=key)
    assert r.status_code == 409  # not a replay any more: the request runs again and meets the business key
    other = api.post("/watchlist", json={**GOOD, "neo_reference_id": BARE}, headers=key)
    assert other.status_code == 201  # and the key is free for a new request


def test_concurrent_retries_with_one_key_create_exactly_one_entry(db_schema):
    """Two simultaneous requests on separate connections (committed, like production): the advisory lock
    makes the second wait for the first, then replay it."""
    cfg, schema = db_schema

    @contextmanager
    def fresh():
        with connect(cfg, connect_timeout=3, options=f"-c search_path={schema}") as c:
            yield c

    with fresh() as conn:
        seed(conn)
        conn.commit()
    try:
        app = create_app(fresh, read_limiter=RateLimiter(100, 100), write_limiter=RateLimiter(100, 100))
        barrier, results = threading.Barrier(2), []

        def attempt() -> None:
            with TestClient(app) as client:
                barrier.wait()
                results.append(ContractClient(client).post("/watchlist", json=GOOD,
                                                           headers={"Idempotency-Key": "concurrent-key-1"}))

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert [r.status_code for r in results] == [201, 201]
        assert sorted(r.headers["Idempotent-Replayed"] for r in results) == ["false", "true"]
        assert results[0].json() == results[1].json()
        with fresh() as conn:
            assert rows(conn) == 1
    finally:
        with fresh() as conn:
            conn.execute("TRUNCATE watchlist, idempotency_keys, anomaly_alerts, orbit_approaches, orbital_elements,"
                         " close_approaches, asteroids")


def test_request_fingerprint_depends_on_content_only():
    a = WatchRequest(ZOOZVE, Priority.HIGH, 20.0, None, ("pha",))
    assert a.fingerprint() == WatchRequest(ZOOZVE, Priority.HIGH, 20.0, None, ("pha",)).fingerprint()
    assert a.fingerprint() != WatchRequest(ZOOZVE, Priority.HIGH, 20.5, None, ("pha",)).fingerprint()
    assert len(a.fingerprint()) == 64
