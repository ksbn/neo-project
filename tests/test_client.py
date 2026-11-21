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

from client import BASE_URL, MAX_RETRY_AFTER_SECONDS, NasaApiError, NeoWsClient, _CappedRetry, date_chunks
from conftest import make_feed, make_neo

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


def test_missing_api_key_is_a_config_error(monkeypatch):
    monkeypatch.setattr("client.load_dotenv", lambda: None)
    monkeypatch.delenv("NASA_API_KEY", raising=False)
    with pytest.raises(ValueError, match="NASA_API_KEY"):
        NeoWsClient()


# --------------------------------------------------------------------------- #
# Bad responses and HTTP errors
# --------------------------------------------------------------------------- #


@responses.activate
def test_malformed_json(client):
    responses.get(FEED, body="<html>oops</html>", content_type="text/html")
    with pytest.raises(NasaApiError, match="non-JSON"):
        client.get_feed(D1, D2)


@responses.activate
def test_json_array_instead_of_object(client):
    responses.get(FEED, json=[1, 2, 3])
    with pytest.raises(NasaApiError, match="expected an object"):
        client.get_feed(D1, D2)


@responses.activate
def test_http_403_surfaces_nasa_message_without_retrying(client):
    responses.get(FEED, status=403, json={"error": {"code": "API_KEY_INVALID", "message": "An invalid api_key was supplied."}})
    with pytest.raises(NasaApiError, match="HTTP 403: An invalid api_key") as exc:
        client.get_feed(D1, D2)
    assert exc.value.status_code == 403
    assert len(responses.calls) == 1


@responses.activate
def test_http_400_uses_neows_error_message(client):
    responses.get(FEED, status=400, json={"code": 400, "error_message": "Date Format Exception"})
    with pytest.raises(NasaApiError, match="HTTP 400: Date Format Exception"):
        client.get_feed(D1, D2)


@responses.activate
def test_503_then_200_is_retried(client):
    responses.get(FEED, status=503)
    responses.get(FEED, json=EMPTY)
    assert client.get_feed(D1, D2)["element_count"] == 0
    assert len(responses.calls) == 2


@responses.activate
def test_persistent_5xx_gives_up_after_retries(client):
    responses.get(FEED, status=502)
    with pytest.raises(NasaApiError, match="HTTP 502") as exc:
        client.get_feed(D1, D2)
    assert exc.value.status_code == 502
    assert len(responses.calls) == 6  # 1 attempt + 5 retries


@responses.activate
def test_persistent_429_explains_rate_limit(client):
    responses.get(FEED, status=429, json={"error": {"code": "OVER_RATE_LIMIT", "message": "rate limited"}})
    with pytest.raises(NasaApiError, match="rate limit exceeded.*personal NASA_API_KEY") as exc:
        client.get_feed(D1, D2)
    assert exc.value.status_code == 429


def test_dns_failure_is_not_reported_as_timeout():
    # .invalid is reserved (RFC 2606) and never resolves.
    with NeoWsClient(api_key="k", base_url="https://api.nasa.invalid/neo", max_retries=1, backoff_factor=0) as c:
        with pytest.raises(NasaApiError, match="Could not resolve api.nasa.invalid") as exc:
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
