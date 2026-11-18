"""HTTP client for NASA's Near Earth Object Web Service (NeoWs) feed endpoint.

The client wraps a ``requests.Session`` configured with automatic retries
(exponential backoff on connection errors, timeouts, HTTP 429 and 5xx), and
translates every failure into a single :class:`NasaApiError` so callers only
need to handle one exception type.

The API key is sent in the ``X-Api-Key`` header rather than the query string,
so it never appears in URLs, exception messages or urllib3 debug logs.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from datetime import date, timedelta
from types import TracebackType
from typing import Any, Final, Self

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.exceptions import MaxRetryError
from urllib3.exceptions import TimeoutError as Urllib3TimeoutError
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

BASE_URL: Final = "https://api.nasa.gov/neo/rest/v1"
# NeoWs rejects feed requests spanning more than 7 days; 7 inclusive days is safe.
MAX_FEED_DAYS: Final = 7
# (connect timeout, read timeout) in seconds.
DEFAULT_TIMEOUT: Final = (5.0, 30.0)
RETRY_STATUS_CODES: Final = frozenset({429, 500, 502, 503, 504})
# Upper bound on a server-supplied Retry-After; urllib3's own default is 6 hours.
MAX_RETRY_AFTER_SECONDS: Final = 60.0


class NasaApiError(RuntimeError):
    """The NeoWs API could not be reached or returned an unusable response."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def date_chunks(start_date: date, end_date: date, max_days: int = MAX_FEED_DAYS) -> Iterator[tuple[date, date]]:
    """Split an inclusive date range into consecutive windows of at most ``max_days`` days.

    >>> list(date_chunks(date(2024, 1, 1), date(2024, 1, 10)))
    [(datetime.date(2024, 1, 1), datetime.date(2024, 1, 7)), (datetime.date(2024, 1, 8), datetime.date(2024, 1, 10))]
    """
    if end_date < start_date:
        raise ValueError(f"end_date ({end_date}) is before start_date ({start_date})")
    if max_days < 1:
        raise ValueError("max_days must be at least 1")

    chunk_start = start_date
    while chunk_start <= end_date:
        chunk_end = min(chunk_start + timedelta(days=max_days - 1), end_date)
        yield chunk_start, chunk_end
        chunk_start = chunk_end + timedelta(days=1)


class _CappedRetry(Retry):
    """Retry policy that never sleeps longer than MAX_RETRY_AFTER_SECONDS for a Retry-After header.

    Without the cap a misbehaving or rate-limiting server could stall the
    pipeline for hours. urllib3 creates follow-up instances via ``type(self)``,
    so the subclass survives across attempts.
    """

    def parse_retry_after(self, retry_after: str) -> float:
        return min(super().parse_retry_after(retry_after), MAX_RETRY_AFTER_SECONDS)


def _build_session(max_retries: int, backoff_factor: float) -> requests.Session:
    """Create a session that retries idempotent GETs with exponential backoff."""
    retry = _CappedRetry(
        total=max_retries,
        connect=max_retries,
        read=max_retries,
        status=max_retries,
        backoff_factor=backoff_factor,  # sleeps 0s, 2s, 4s, 8s, ... (x backoff_factor)
        status_forcelist=RETRY_STATUS_CODES,
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        # Return the final failed response instead of raising, so we can
        # report the API's own error message.
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    # Mount for both schemes so a plain-http base_url (e.g. a local test server) retries too.
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"Accept": "application/json", "User-Agent": "neo-pipeline/1.0"})
    return session


def _is_timeout(exc: requests.RequestException) -> bool:
    """True if ``exc`` was ultimately caused by a connect or read timeout.

    Once urllib3 exhausts its retries on read timeouts, requests raises a plain
    ``ConnectionError`` wrapping ``MaxRetryError(reason=ReadTimeoutError)``
    rather than ``requests.Timeout``, so the cause has to be unwrapped.
    """
    if isinstance(exc, requests.Timeout):
        return True
    inner = exc.args[0] if exc.args else None
    return isinstance(inner, MaxRetryError) and isinstance(inner.reason, Urllib3TimeoutError)


def _error_detail(response: requests.Response) -> str:
    """Extract a short, human-readable error message from a failed response."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:200].strip() or response.reason or "no details"

    if isinstance(body, dict):
        # NeoWs errors: {"error_message": ...}; api.data.gov gateway errors: {"error": {"message": ...}}
        if isinstance(body.get("error_message"), str):
            return body["error_message"]
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
    return response.reason or "no details"


class NeoWsClient:
    """Client for the NeoWs ``/feed`` endpoint.

    Use as a context manager so the underlying HTTP session is closed::

        with NeoWsClient() as client:
            payload = client.get_feed(date(2024, 1, 1), date(2024, 1, 7))
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = BASE_URL,
        timeout: tuple[float, float] = DEFAULT_TIMEOUT,
        max_retries: int = 5,
        backoff_factor: float = 1.0,
        session: requests.Session | None = None,
    ) -> None:
        """Create a client.

        Args:
            api_key: NASA API key. Defaults to ``NASA_API_KEY`` from the environment / ``.env``.
            base_url: NeoWs base URL (overridable for testing).
            timeout: ``(connect, read)`` timeouts in seconds for each attempt.
            max_retries: Maximum retry attempts for transient failures.
            backoff_factor: Multiplier for the exponential backoff between retries.
            session: Pre-configured session (mainly for tests); retries are not added to it.
        """
        if api_key is None:
            load_dotenv()
            api_key = os.getenv("NASA_API_KEY")
        if not api_key:
            raise ValueError("NASA_API_KEY is not set; add it to .env (see .env.example)")

        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._session = session or _build_session(max_retries, backoff_factor)
        self._session.headers["X-Api-Key"] = api_key

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self._session.close()

    def get_feed(self, start_date: date, end_date: date) -> dict[str, Any]:
        """Fetch the raw NeoWs feed for an inclusive range of at most 7 days.

        Raises:
            ValueError: If the range is inverted or longer than 7 days.
            NasaApiError: On network failure, timeout, non-2xx status or invalid JSON.
        """
        if end_date < start_date:
            raise ValueError(f"end_date ({end_date}) is before start_date ({start_date})")
        if (end_date - start_date).days >= MAX_FEED_DAYS:
            raise ValueError(f"NeoWs feed range is limited to {MAX_FEED_DAYS} days; use iter_feed() for longer ranges")

        return self._get(
            "feed",
            params={"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        )

    def iter_feed(self, start_date: date, end_date: date) -> Iterator[tuple[date, date, dict[str, Any]]]:
        """Fetch an arbitrarily long date range, one 7-day window at a time.

        Yields:
            ``(chunk_start, chunk_end, payload)`` for each window, in chronological order.
        """
        for chunk_start, chunk_end in date_chunks(start_date, end_date):
            yield chunk_start, chunk_end, self.get_feed(chunk_start, chunk_end)

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        """GET ``path`` and return the decoded JSON object, raising NasaApiError on failure."""
        url = f"{self._base_url}/{path.lstrip('/')}"
        logger.debug("GET %s params=%s", url, params)

        try:
            response = self._session.get(url, params=params, timeout=self._timeout)
        except requests.RequestException as exc:
            if _is_timeout(exc):
                raise NasaApiError(
                    f"Timed out calling NeoWs /{path} after retries "
                    f"(connect {self._timeout[0]:g}s / read {self._timeout[1]:g}s per attempt)"
                ) from exc
            raise NasaApiError(f"Network error calling NeoWs /{path}: {exc}") from exc

        remaining = response.headers.get("X-RateLimit-Remaining")
        if remaining is not None:
            logger.debug("NASA API rate limit remaining: %s", remaining)

        if response.status_code == 429:
            raise NasaApiError(
                f"NeoWs /{path} rate limit exceeded (HTTP 429) after retries. DEMO_KEY allows only a few "
                "requests per hour; set a personal NASA_API_KEY from https://api.nasa.gov in .env",
                status_code=429,
            )
        if not response.ok:
            raise NasaApiError(
                f"NeoWs /{path} returned HTTP {response.status_code}: {_error_detail(response)}",
                status_code=response.status_code,
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise NasaApiError(f"NeoWs /{path} returned a non-JSON response") from exc
        if not isinstance(payload, dict):
            raise NasaApiError(f"NeoWs /{path} returned JSON of type {type(payload).__name__}, expected an object")
        return payload
