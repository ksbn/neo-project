"""The implementation and openapi.yaml agree: same operations, same request rules, same error shape.

(Every response in tests/api is also schema-checked by ContractClient.)
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from planetary_defense.api.app import PREFIX, create_app
from planetary_defense.api.errors import RateLimiter
from planetary_defense.domain.errors import StorageError

from .conftest import BARE, FAST, KF54, NEAR, ZOOZVE, app_for
from contract import SPEC, ContractClient, operations, parameter_schema, schema_errors


def test_every_operation_in_the_contract_is_implemented_and_nothing_else_is():
    app = create_app(lambda: (_ for _ in ()).throw(AssertionError("no database needed")))  # type: ignore[arg-type]
    routed = {(m, r.path.removeprefix(PREFIX)) for r in app.routes if isinstance(r, APIRoute)
              and r.path.startswith(PREFIX) for m in (r.methods or ())}
    assert routed == operations()


def test_every_error_response_uses_the_shared_error_shape():
    for path, item in SPEC["paths"].items():
        for method, op in item.items():
            for status, response in op["responses"].items():
                if int(status) >= 400:
                    assert response["$ref"].startswith("#/components/responses/"), (method, path, status)
    for name, response in SPEC["components"]["responses"].items():
        schema = response["content"]["application/json"]["schema"]
        assert schema == {"$ref": "#/components/schemas/ErrorResponse"}, name


# Bodies judged by the contract's JSON Schema and by the API: the two must agree.
WRITE = "/components/schemas/WatchlistEntryCreate"
BODIES = [
    ({"neo_reference_id": ZOOZVE, "priority": "high"}, True),
    ({"neo_reference_id": KF54, "priority": "low", "alert_threshold_ld": 100, "note": "", "tags": []}, True),
    ({"neo_reference_id": NEAR, "priority": "critical", "alert_threshold_ld": 0.5, "note": None,
      "tags": ["a", "b-2", "c", "d", "e"]}, True),
    ({"neo_reference_id": FAST, "priority": "medium", "alert_threshold_ld": None, "tags": ["x" * 24]}, True),
    ({"neo_reference_id": BARE, "priority": "low", "alert_threshold_ld": 1}, True),  # an integer is a number
    ({"priority": "high"}, False),
    ({"neo_reference_id": ZOOZVE}, False),
    ({"neo_reference_id": "abc", "priority": "high"}, False),
    ({"neo_reference_id": 2524522, "priority": "high"}, False),
    ({"neo_reference_id": ZOOZVE, "priority": "HIGH"}, False),
    ({"neo_reference_id": ZOOZVE, "priority": "high", "alert_threshold_ld": 0}, False),
    ({"neo_reference_id": ZOOZVE, "priority": "high", "alert_threshold_ld": 100.01}, False),
    ({"neo_reference_id": ZOOZVE, "priority": "high", "alert_threshold_ld": "20"}, False),
    ({"neo_reference_id": ZOOZVE, "priority": "high", "alert_threshold_ld": True}, False),
    ({"neo_reference_id": ZOOZVE, "priority": "high", "note": "x" * 501}, False),
    ({"neo_reference_id": ZOOZVE, "priority": "high", "tags": ["a"] * 2}, False),
    ({"neo_reference_id": ZOOZVE, "priority": "high", "tags": list("abcdef")}, False),
    ({"neo_reference_id": ZOOZVE, "priority": "high", "tags": ["-dash-first"]}, False),
    ({"neo_reference_id": ZOOZVE, "priority": "high", "tags": ["x" * 25]}, False),
    ({"neo_reference_id": ZOOZVE, "priority": "high", "extra": 1}, False),
]


@pytest.mark.parametrize(("body", "valid"), BODIES)
def test_the_api_accepts_exactly_the_bodies_the_contract_accepts(api, body, valid):
    assert (not schema_errors(WRITE, body)) is valid, "the test case itself disagrees with the contract"
    r = api.post("/watchlist", json=body)
    assert r.status_code == (201 if valid else 422), r.json()


QUERY = [
    ("limit", 1, True), ("limit", 100, True), ("limit", 0, False), ("limit", 101, False), ("limit", "ten", False),
    ("offset", 0, True), ("offset", -1, False),
    ("max_distance_ld", 0.5, True), ("max_distance_ld", 0, False),
    ("min_velocity_km_s", 0, True), ("min_velocity_km_s", -1, False),
    ("min_severity", "watch", True), ("min_severity", "severe", False),
    ("sort", "-velocity", True), ("sort", "size", False),
    ("approach_from", "2026-11-01", True), ("approach_from", "11/01/2026", False),
    ("q", "z", True), ("q", "x" * 101, False),
]


@pytest.mark.parametrize(("name", "value", "valid"), QUERY)
def test_query_parameters_follow_the_contract(api, name, value, valid):
    from jsonschema import Draft202012Validator

    schema = parameter_schema("GET", "/objects", name)
    as_typed = value if not isinstance(value, str) or schema.get("type") == "string" else value
    assert Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).is_valid(as_typed) is valid
    r = api.get("/objects", params={name: value})
    assert r.status_code == (200 if valid else 400)
    if not valid:
        assert r.json()["details"][0]["field"] == f"query.{name}"


def test_a_malformed_path_id_is_400(api):
    r = api.get("/objects/{neo_reference_id}", path_params={"neo_reference_id": "abc"})
    assert r.status_code == 400 and r.json()["details"][0]["field"] == "path.neo_reference_id"


def test_reversed_date_range_is_400(api):
    r = api.get("/objects", params={"approach_from": "2026-11-20", "approach_to": "2026-11-01"})
    assert r.status_code == 400 and r.json()["code"] == "BAD_REQUEST"


# --------------------------------------------------------------------------- #
# 429, 500, 503 and the request ID
# --------------------------------------------------------------------------- #


def test_too_many_requests_is_429_with_retry_after(seeded):
    clock = [0.0]
    limiter = RateLimiter(capacity=2, per_second=0.5, clock=lambda: clock[0])
    with TestClient(app_for(seeded, reads=limiter), raise_server_exceptions=False) as client:
        api = ContractClient(client)
        assert [api.get("/objects").status_code for _ in range(3)] == [200, 200, 429]
        r = api.get("/objects")
        assert r.json()["code"] == "RATE_LIMITED" and r.headers["Retry-After"] == "2"
        clock[0] += 2  # one token refilled
        assert api.get("/objects").status_code == 200


def test_a_database_outage_is_503_without_driver_details():
    @contextmanager
    def down():
        raise StorageError("connection to server at 10.0.0.5 failed: password authentication failed for neo_user")
        yield  # pragma: no cover

    with TestClient(create_app(down), raise_server_exceptions=False) as client:
        r = ContractClient(client).get("/objects")
    assert r.status_code == 503 and r.json()["code"] == "STORAGE_UNAVAILABLE" and r.headers["Retry-After"] == "5"
    assert "10.0.0.5" not in r.text and "neo_user" not in r.text


def test_a_bug_is_500_without_a_stack_trace(caplog):
    @contextmanager
    def broken():
        raise RuntimeError("secret internals")
        yield  # pragma: no cover

    with TestClient(create_app(broken), raise_server_exceptions=False) as client:
        r = ContractClient(client).get("/objects")
    assert r.status_code == 500 and r.json()["code"] == "INTERNAL_ERROR"
    assert "secret internals" not in r.text and "secret internals" in caplog.text  # logged, not leaked


def test_the_request_id_is_echoed_or_generated(api):
    mine = api.get("/objects/{neo_reference_id}", path_params={"neo_reference_id": "9999999"},
                   headers={"X-Request-ID": "trace-1234abcd"})
    assert mine.headers["X-Request-ID"] == mine.json()["request_id"] == "trace-1234abcd"
    generated = api.get("/objects", headers={"X-Request-ID": "<script>"})  # unsafe values are replaced
    assert generated.headers["X-Request-ID"] != "<script>" and len(generated.headers["X-Request-ID"]) == 16


def test_the_contract_is_served(api):
    r = api.client.get("/api/openapi.yaml")
    assert r.status_code == 200 and r.text.startswith("openapi: 3.1")
