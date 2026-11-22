"""Every error leaves the API as one ``ErrorResponse`` (see ``openapi.yaml``).

* :data:`API_OUTCOMES` maps domain errors to a status and code, resolved through the
  class's MRO like ``cli.CLI_OUTCOMES``. Storage and unexpected errors get a generic
  message: driver text and stack traces stay in the server log, under the request ID.
* :func:`classify` decides 400 vs 422 for validation errors: a request that could not
  be read (bad JSON, parameters, headers) is 400; a readable body that breaks the
  rules is 422.
* :class:`RateLimiter` is a per-client token bucket behind the ``429`` responses.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

from fastapi import Request
from fastapi.responses import JSONResponse

from planetary_defense.api.schemas import ErrorCode, ErrorDetail, ErrorResponse
from planetary_defense.domain.errors import (
    AlreadyWatched,
    IdempotencyKeyReused,
    NeoError,
    NotFound,
    StorageError,
    UnknownObject,
)

logger = logging.getLogger(__name__)

STORAGE_RETRY_AFTER_S: Final = 5


@dataclass
class ApiError(Exception):
    status: int
    code: ErrorCode
    message: str
    details: list[ErrorDetail] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)


# Most specific class wins; NeoError catches the rest.
API_OUTCOMES: Final[dict[type[NeoError], tuple[int, ErrorCode, str | None]]] = {
    NotFound: (404, "NOT_FOUND", None),
    AlreadyWatched: (409, "ALREADY_WATCHED", "body.neo_reference_id"),
    UnknownObject: (422, "UNKNOWN_OBJECT", "body.neo_reference_id"),
    IdempotencyKeyReused: (422, "IDEMPOTENCY_KEY_REUSED", "header.Idempotency-Key"),
    StorageError: (503, "STORAGE_UNAVAILABLE", None),
    NeoError: (500, "INTERNAL_ERROR", None),
}
_GENERIC: Final[dict[ErrorCode, str]] = {
    "STORAGE_UNAVAILABLE": "The database is unavailable right now; retry later.",
    "INTERNAL_ERROR": "An unexpected error occurred.",
}


def from_domain(exc: NeoError) -> ApiError:
    for kind in type(exc).__mro__:
        if kind in API_OUTCOMES:
            status, code, where = API_OUTCOMES[kind]
            break
    else:  # pragma: no cover - NeoError is always in the table
        status, code, where = 500, "INTERNAL_ERROR", None
    message = _GENERIC.get(code, str(exc))
    details = [ErrorDetail(field=where, reason=str(exc))] if where else []
    headers = {"Retry-After": str(STORAGE_RETRY_AFTER_S)} if code == "STORAGE_UNAVAILABLE" else {}
    return ApiError(status, code, message, details, headers)


def _where(loc: Sequence[Any]) -> str | None:
    return ".".join(str(part) for part in loc) if loc else None


def details_of(errors: Iterable[Mapping[str, Any]], prefix: str = "") -> list[ErrorDetail]:
    """Pydantic/FastAPI errors as ``{field, reason}``, e.g. ``query.limit`` or ``body.tags.1``."""
    out = []
    for err in errors:
        loc = [prefix, *err.get("loc", ())] if prefix else list(err.get("loc", ()))
        out.append(ErrorDetail(field=_where(loc), reason=str(err.get("msg", "invalid"))))
    return out


def classify(errors: Sequence[Mapping[str, Any]], *, body: bool) -> ApiError:
    """400 for a request that can't be read; 422 for a readable body that breaks the rules."""
    unreadable = not body or any(e.get("type") == "json_invalid" or not e.get("loc") for e in errors)
    if unreadable:
        what = "body is not valid JSON" if body else "request has invalid parameters"
        return ApiError(400, "BAD_REQUEST", f"The {what}.", details_of(errors, "body" if body else ""))
    return ApiError(422, "VALIDATION_FAILED", "The request body failed validation.", details_of(errors, "body"))


def error_response(request: Request, err: ApiError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    body = ErrorResponse(code=err.code, message=err.message, details=err.details, request_id=request_id,
                         timestamp=datetime.now(UTC))
    return JSONResponse(body.model_dump(mode="json"), status_code=err.status,
                        headers={**err.headers, "X-Request-ID": request_id})


# --------------------------------------------------------------------------- #
# Rate limiting
# --------------------------------------------------------------------------- #


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    """Token bucket per client: ``capacity`` requests at once, refilled at ``per_second``.

    In-process, so per server process; behind several processes it would live in Redis
    or at the gateway instead.
    """

    def __init__(self, capacity: int, per_second: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.capacity, self.per_second, self._clock = capacity, per_second, clock
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def check(self, client: str) -> None:
        """Take one token, or raise a 429 ApiError with Retry-After."""
        with self._lock:
            now = self._clock()
            b = self._buckets.setdefault(client, _Bucket(float(self.capacity), now))
            b.tokens = min(self.capacity, b.tokens + (now - b.updated) * self.per_second)
            b.updated = now
            if b.tokens >= 1:
                b.tokens -= 1
                return
            wait = max(1, int((1 - b.tokens) / self.per_second + 0.999))
        raise ApiError(429, "RATE_LIMITED", f"Too many requests; retry in {wait} seconds.",
                       headers={"Retry-After": str(wait)})
