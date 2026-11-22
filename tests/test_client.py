"""HTTP layer (client.py), with the NASA API simulated so tests never touch the network.

``responses`` intercepts requests at the adapter level but still drives
urllib3's Retry logic, so retry counts are realistic. Timeouts need a real
socket, so those tests use a tiny local HTTP server instead.
"""

import threading
import time
from collections.abc import Iterator
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests
import responses
from responses import matchers

from client import (
    BASE_URL,
    INTERACTIVE,
    MAX_RETRY_AFTER_SECONDS,
    NasaApiError,
    NeoWsClient,
    _CappedRetry,
    date_chunks,
)
from conftest import make_feed, make_neo
from errors import (
    FeedContractError,
    UpstreamAuthError,
    UpstreamError,
    UpstreamRateLimited,
    UpstreamUnavailable,
)

FEED = f"{BASE_URL}/feed"
D1, D2 = date(2026, 11, 10), date(2026, 11, 12)
EMPTY = {"element_count": 0, "near_earth_objects": {}}


@pytest.fixture
def client() -> Iterator[NeoWsClient]:
    # backoff_factor=0 keeps retry tests instant
    with NeoWsClient(api_key="test-key", backoff_factor=0) as c:
        yield c


# --------------------------------------------------------------------------- #
# Successful responses
# --------------------------------------------------------------------------- #


@responses.activate
def test_valid_payload_sends_key_in_header_not_url(client):
    responses.get(
        FEED,
        json=make_feed({"2026-11-10": [make_neo()]}),
        match=[
            matchers.header_matcher({"X-Api-Key": "test-key"}),
            matchers.query_param_matcher({"start_date": "2026-11-10", "end_date": "2026-11-12"}),
        ],
    )
    assert client.get_feed(D1, D2)["element_count"] == 1
    assert "test-key" not in responses.calls[0].request.url


@responses.activate
def test_empty_asteroid_list(client):
    responses.get(FEED, json=EMPTY)
    assert client.get_feed(D1, D2)["near_earth_objects"] == {}


def test_empty_api_key_is_rejected():
    # Reading the key from the environment is config.Settings' job (see test_config.py).
    with pytest.raises(ValueError, match="api_key"):
        NeoWsClient("")


@responses.activate
def test_fetch_records_returns_validated_records(client):
    responses.get(FEED, json=make_feed({"2026-11-10": [make_neo("1")]}))
    (record,) = client.fetch_records(D1, D2).records
    assert record.neo_reference_id == "1"


@responses.activate
def test_fetch_records_reports_schema_drift_as_contract_error(client):
    responses.get(FEED, json={"unexpected": True})
    with pytest.raises(FeedContractError, match="expected format"):
        client.fetch_records(D1, D2)


# --------------------------------------------------------------------------- #
# Bad responses and HTTP errors
# --------------------------------------------------------------------------- #


@responses.activate
def test_malformed_json(client):
    responses.get(FEED, body="<html>oops</html>", content_type="text/html")
    with pytest.raises(FeedContractError, match="non-JSON"):
        client.get_feed(D1, D2)


@responses.activate
def test_json_array_instead_of_object(client):
    responses.get(FEED, json=[1, 2, 3])
    with pytest.raises(FeedContractError, match="expected an object"):
        client.get_feed(D1, D2)


@responses.activate
def test_http_403_is_an_auth_error_without_retrying(client):
    responses.get(FEED, status=403, json={"error": {"code": "API_KEY_INVALID", "message": "An invalid api_key was supplied."}})
    with pytest.raises(UpstreamAuthError, match="HTTP 403: An invalid api_key.*NASA_API_KEY") as exc:
        client.get_feed(D1, D2)
    assert exc.value.status_code == 403
    assert len(responses.calls) == 1


@responses.activate
def test_http_400_uses_neows_error_message(client):
    responses.get(FEED, status=400, json={"code": 400, "error_message": "Date Format Exception"})
    with pytest.raises(UpstreamError, match="HTTP 400: Date Format Exception") as exc:
        client.get_feed(D1, D2)
    assert type(exc.value) is UpstreamError  # neither retryable nor an auth problem


@responses.activate
def test_503_then_200_is_retried(client):
    responses.get(FEED, status=503)
    responses.get(FEED, json=EMPTY)
    assert client.get_feed(D1, D2)["element_count"] == 0
    assert len(responses.calls) == 2


@responses.activate
def test_persistent_5xx_gives_up_after_retries(client):
    responses.get(FEED, status=502)
    with pytest.raises(UpstreamUnavailable, match="HTTP 502") as exc:
        client.get_feed(D1, D2)
    assert exc.value.status_code == 502
    assert len(responses.calls) == 6  # 1 attempt + 5 retries


@responses.activate
def test_persistent_429_explains_rate_limit(client):
    responses.get(FEED, status=429, json={"error": {"code": "OVER_RATE_LIMIT", "message": "rate limited"}})
    with pytest.raises(UpstreamRateLimited, match="rate limit exceeded.*personal NASA_API_KEY") as exc:
        client.get_feed(D1, D2)
    assert exc.value.status_code == 429


def test_dns_failure_is_not_reported_as_timeout():
    # .invalid is reserved (RFC 2606) and never resolves.
    with NeoWsClient(api_key="k", base_url="https://api.nasa.invalid/neo", max_retries=1, backoff_factor=0) as c:
        with pytest.raises(UpstreamUnavailable, match="Could not resolve api.nasa.invalid") as exc:
            c.get_feed(D1, D1)
    assert "Timed out" not in str(exc.value)


def test_refused_connection_is_not_reported_as_timeout():
    with NeoWsClient(api_key="k", base_url="http://127.0.0.1:1", max_retries=1, backoff_factor=0) as c:
        with pytest.raises(NasaApiError, match="Network error") as exc:
            c.get_feed(D1, D1)
    assert "Timed out" not in str(exc.value)


@responses.activate
def test_connection_error(client):
    responses.get(FEED, body=requests.ConnectionError("boom"))
    with pytest.raises(NasaApiError, match="Network error"):
        client.get_feed(D1, D2)


# --------------------------------------------------------------------------- #
# Retry policy
# --------------------------------------------------------------------------- #


def test_retry_after_is_capped():
    assert _CappedRetry().parse_retry_after("21600") == MAX_RETRY_AFTER_SECONDS
    assert _CappedRetry().parse_retry_after("3") == 3


def test_retries_apply_to_http_and_https(client):
    for url in ("https://example.test", "http://example.test"):
        assert isinstance(client._session.get_adapter(url).max_retries, _CappedRetry)


def test_every_failure_is_still_a_nasa_api_error():
    """NasaApiError stays a catch-all alias for older callers."""
    for kind in (UpstreamUnavailable, UpstreamRateLimited, UpstreamAuthError, FeedContractError):
        assert issubclass(kind, NasaApiError)


def test_interactive_profile_retries_once_and_ignores_retry_after():
    with NeoWsClient("k", profile=INTERACTIVE) as c:
        retry = c._session.get_adapter(BASE_URL).max_retries
        assert retry.total == 1
        assert retry.respect_retry_after_header is False
        assert c._timeout == INTERACTIVE.timeout


@responses.activate
def test_interactive_profile_does_not_sleep_on_retry_after():
    responses.get(FEED, status=429, headers={"Retry-After": "30"})
    responses.get(FEED, json=EMPTY)
    started = time.monotonic()
    with NeoWsClient("k", profile=INTERACTIVE, backoff_factor=0) as c:
        assert c.get_feed(D1, D2)["element_count"] == 0
    assert time.monotonic() - started < 5
    assert len(responses.calls) == 2


# --------------------------------------------------------------------------- #
# Timeouts (real local server)
# --------------------------------------------------------------------------- #


@pytest.fixture
def slow_server() -> Iterator[tuple[str, list[int]]]:
    """Local HTTP server that answers every request after 2 seconds."""
    hits: list[int] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep test output quiet
            pass

        def do_GET(self):
            hits.append(1)
            time.sleep(2)
            try:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")
            except OSError:  # client already gave up
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", hits
    server.shutdown()


def test_read_timeout_is_retried_then_reported_as_timeout(slow_server):
    base_url, hits = slow_server
    with NeoWsClient(api_key="k", base_url=base_url, timeout=(1, 0.3), max_retries=2, backoff_factor=0) as c:
        with pytest.raises(NasaApiError, match="Timed out .* read 0.3s per attempt"):
            c.get_feed(D1, D1)
    assert len(hits) == 3  # 1 attempt + 2 retries


# --------------------------------------------------------------------------- #
# Date range handling
# --------------------------------------------------------------------------- #


def test_range_over_7_days_rejected_without_http(client):
    with pytest.raises(ValueError, match="7 days"):
        client.get_feed(date(2026, 11, 1), date(2026, 11, 8))


def test_inverted_range_rejected(client):
    with pytest.raises(ValueError, match="before"):
        client.get_feed(D2, D1)


def test_date_chunks_split_long_ranges():
    assert list(date_chunks(date(2026, 11, 1), date(2026, 11, 15))) == [
        (date(2026, 11, 1), date(2026, 11, 7)),
        (date(2026, 11, 8), date(2026, 11, 14)),
        (date(2026, 11, 15), date(2026, 11, 15)),
    ]
