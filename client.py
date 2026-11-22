"""HTTP client for NASA's Near Earth Object Web Service (NeoWs) feed endpoint.

The client wraps a ``requests.Session`` configured with automatic retries
(exponential backoff on connection errors, timeouts, HTTP 429 and 5xx), and
translates every failure into an :class:`errors.UpstreamError` subclass that
says whether retrying can help (auth vs rate limit vs outage vs format change).

Two retry profiles exist because the callers differ: the batch pipeline can
wait out a ``Retry-After``; the dashboard has a person waiting on the page.

The API key is sent in the ``X-Api-Key`` header rather than the query string,
so it never appears in URLs, exception messages or urllib3 debug logs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from types import TracebackType
from typing import Any, Final, Self
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.exceptions import MaxRetryError, NameResolutionError, NewConnectionError
from urllib3.exceptions import TimeoutError as Urllib3TimeoutError
from urllib3.util.retry import Retry

from domain import FeedBatch
from errors import (
    FeedContractError,
    UpstreamAuthError,
    UpstreamError,
    UpstreamRateLimited,
    UpstreamRequestError,
    UpstreamUnavailable,
)
from models import parse_feed

logger = logging.getLogger(__name__)

BASE_URL: Final = "https://api.nasa.gov/neo/rest/v1"
# NeoWs rejects feed requests spanning more than 7 days; 7 inclusive days is safe.
MAX_FEED_DAYS: Final = 7
RETRY_STATUS_CODES: Final = frozenset({429, 500, 502, 503, 504})
# Upper bound on a server-supplied Retry-After; urllib3's own default is 6 hours.
MAX_RETRY_AFTER_SECONDS: Final = 60.0
DEFAULT_USER_AGENT: Final = "neo-pipeline/1.0"


# Backwards-compatible name: catches every NASA failure, whatever its category.
NasaApiError = UpstreamError


@dataclass(frozen=True)
class RetryProfile:
    """How patient a caller is with NASA."""

    timeout: tuple[float, float]  # (connect, read) seconds per attempt
    max_retries: int
    respect_retry_after: bool


# Unattended pipeline: wait out rate limits (capped at MAX_RETRY_AFTER_SECONDS).
BATCH: Final = RetryProfile(timeout=(5.0, 30.0), max_retries=5, respect_retry_after=True)
# Dashboard: one quick retry, never sleep on Retry-After. Worst case is about 2 x (3 + 8) s.
INTERACTIVE: Final = RetryProfile(timeout=(3.0, 8.0), max_retries=1, respect_retry_after=False)


class _CappedRetry(Retry):
    """Retry policy that never sleeps longer than MAX_RETRY_AFTER_SECONDS for a Retry-After header.

    Without the cap a misbehaving or rate-limiting server could stall the
    pipeline for hours. urllib3 creates follow-up instances via ``type(self)``,
    so the subclass survives across attempts.
    """

    def parse_retry_after(self, retry_after: str) -> float:
        return min(super().parse_retry_after(retry_after), MAX_RETRY_AFTER_SECONDS)


def _build_session(
    max_retries: int, backoff_factor: float, respect_retry_after: bool, user_agent: str
) -> requests.Session:
    """Create a session that retries idempotent GETs with exponential backoff."""
    retry = _CappedRetry(
        total=max_retries,
        connect=max_retries,
        read=max_retries,
        status=max_retries,
        backoff_factor=backoff_factor,  # sleeps 0s, 2s, 4s, 8s, ... (x backoff_factor)
        status_forcelist=RETRY_STATUS_CODES,
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=respect_retry_after,
        # Return the final failed response instead of raising, so we can
        # report the API's own error message.
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    # Mount for both schemes so a plain-http base_url (e.g. a local test server) retries too.
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"Accept": "application/json", "User-Agent": user_agent})
    return session


def _is_timeout(exc: requests.RequestException) -> bool:
    """True if ``exc`` was ultimately caused by a connect or read timeout.

    Once urllib3 exhausts its retries on read timeouts, requests raises a plain
    ``ConnectionError`` wrapping ``MaxRetryError(reason=ReadTimeoutError)``
    rather than ``requests.Timeout``, so the cause has to be unwrapped.
    """
    if isinstance(exc, requests.Timeout):
        return True
    reason = _retry_reason(exc)
    # urllib3 makes NewConnectionError (refused connection, failed DNS lookup)
    # a subclass of ConnectTimeoutError for backwards compatibility; it is not a timeout.
    return isinstance(reason, Urllib3TimeoutError) and not isinstance(reason, NewConnectionError)


def _retry_reason(exc: requests.RequestException) -> BaseException | None:
    """The underlying urllib3 error that made the final retry fail, if any."""
    inner = exc.args[0] if exc.args else None
    return inner.reason if isinstance(inner, MaxRetryError) else None


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
    """Client for the NeoWs ``/feed`` endpoint. Satisfies :class:`ports.FeedSource`.

    Use as a context manager so the underlying HTTP session is closed::

        with NeoWsClient(settings.nasa_api_key()) as client:
            batch = client.fetch_records(date(2024, 1, 1), date(2024, 1, 7))
    """

    #: Longest window ``fetch_records`` accepts; callers split ranges with ``domain.date_windows``.
    max_window_days: int = MAX_FEED_DAYS

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        profile: RetryProfile = BATCH,
        timeout: tuple[float, float] | None = None,
        max_retries: int | None = None,
        backoff_factor: float = 1.0,
        user_agent: str = DEFAULT_USER_AGENT,
        session: requests.Session | None = None,
    ) -> None:
        """Create a client.

        Args:
            api_key: NASA API key (see ``config.Settings.nasa_api_key``).
            base_url: NeoWs base URL (overridable for testing).
            profile: Retry/timeout preset; ``timeout`` and ``max_retries`` override its values.
            timeout: ``(connect, read)`` timeouts in seconds for each attempt.
            max_retries: Maximum retry attempts for transient failures.
            backoff_factor: Multiplier for the exponential backoff between retries.
            user_agent: Identifies the caller in NASA's logs (pipeline vs dashboard).
            session: Pre-configured session (mainly for tests); retries are not added to it.
        """
        if not api_key:
            raise ValueError("api_key must be a non-empty string")

        self._base_url = base_url.rstrip("/")
        self._timeout = timeout or profile.timeout
        retries = profile.max_retries if max_retries is None else max_retries
        self._session = session or _build_session(
            retries, backoff_factor, profile.respect_retry_after, user_agent
        )
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
            ValueError: If the range is inverted or longer than 7 days (a caller bug).
            UpstreamError: A subclass describing the failure; see ``errors.py``.
        """
        if end_date < start_date:
            raise ValueError(f"end_date ({end_date}) is before start_date ({start_date})")
        if (end_date - start_date).days >= MAX_FEED_DAYS:
            raise ValueError(f"NeoWs feed range is limited to {MAX_FEED_DAYS} days; split it with domain.date_windows()")

        return self._get(
            "feed",
            params={"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
        )

    def fetch_records(self, start_date: date, end_date: date) -> FeedBatch:
        """Fetch and validate one window: NASA's raw JSON never leaves the adapter.

        Raises:
            UpstreamError: Transport/HTTP failure, or ``FeedContractError`` if the envelope is malformed.
        """
        return parse_feed(self.get_feed(start_date, end_date))

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        """GET ``path`` and return the decoded JSON object, raising an UpstreamError subclass on failure."""
        url = f"{self._base_url}/{path.lstrip('/')}"
        logger.debug("GET %s params=%s", url, params)

        try:
            response = self._session.get(url, params=params, timeout=self._timeout)
        except requests.RequestException as exc:
            if _is_timeout(exc):
                raise UpstreamUnavailable(
                    f"Timed out calling NeoWs /{path} after retries "
                    f"(connect {self._timeout[0]:g}s / read {self._timeout[1]:g}s per attempt)"
                ) from exc
            if isinstance(_retry_reason(exc), NameResolutionError):
                host = urlsplit(url).hostname
                raise UpstreamUnavailable(
                    f"Could not resolve {host} after retries; is the internet connection up?"
                ) from exc
            raise UpstreamUnavailable(f"Network error calling NeoWs /{path}: {exc}") from exc

        remaining = response.headers.get("X-RateLimit-Remaining")
        if remaining is not None:
            logger.debug("NASA API rate limit remaining: %s", remaining)

        status = response.status_code
        if status == 429:
            raise UpstreamRateLimited(
                f"NeoWs /{path} rate limit exceeded (HTTP 429) after retries. DEMO_KEY allows only a few "
                "requests per hour; set a personal NASA_API_KEY from https://api.nasa.gov in .env",
                status_code=429,
            )
        if not response.ok:
            message = f"NeoWs /{path} returned HTTP {status}: {_error_detail(response)}"
            if status in (401, 403):
                raise UpstreamAuthError(f"{message} (check NASA_API_KEY in .env)", status_code=status)
            if status >= 500:
                raise UpstreamUnavailable(message, status_code=status)
            raise UpstreamRequestError(message, status_code=status)

        try:
            payload = response.json()
        except ValueError as exc:
            raise FeedContractError(f"NeoWs /{path} returned a non-JSON response") from exc
        if not isinstance(payload, dict):
            raise FeedContractError(f"NeoWs /{path} returned JSON of type {type(payload).__name__}, expected an object")
        return payload
