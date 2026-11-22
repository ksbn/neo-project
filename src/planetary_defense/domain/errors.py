"""The typed error hierarchy shared by every module. Classification only.

Rule of thumb: the adapters in ``infrastructure/`` (``nasa_client.py``,
``nasa_dto.py``, ``repository.py``, ``db.py``) catch their library's exceptions
(``requests``, ``pydantic``, ``psycopg``) and re-raise one of the
:class:`NeoError` subclasses below, keeping the original as ``__cause__``.
Entry points catch only ``NeoError``; anything else is a bug and is allowed to
crash with a traceback.

How an error is *presented* is decided at each edge, not here:
``cli.CLI_OUTCOMES`` (exit code + log label) and
``ui.messages.describe`` (dashboard text). Both resolve by walking the class's
MRO, so a new subclass is presented like its nearest mapped parent.
"""

from __future__ import annotations


class NeoError(Exception):
    """Base for expected, operational failures."""


class ConfigError(NeoError):
    """Missing or invalid settings. Fix the configuration; retrying will not help."""


class UpstreamError(NeoError):
    """The NASA NeoWs API could not be reached or returned an unusable response."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class UpstreamUnavailable(UpstreamError):
    """Network failure, timeout or HTTP 5xx after all retries. Try again later."""


class UpstreamRateLimited(UpstreamError):
    """HTTP 429 after all retries. Try again later or use a personal API key."""


class UpstreamAuthError(UpstreamError):
    """HTTP 401/403: the API key is missing, invalid or revoked."""


class UpstreamRequestError(UpstreamError):
    """Another HTTP 4xx: NASA rejected our request. Retrying the same request won't help."""


class FeedContractError(UpstreamError):
    """NASA answered, but not in the documented format (schema drift, HTML error page)."""


class StorageError(NeoError):
    """PostgreSQL could not be reached, or a statement failed."""


class OrbitUnavailable(NeoError):
    """No usable orbit for an asteroid.

    NASA has none, it isn't elliptical, or NASA is unreachable and none is cached.
    """


class NotFound(NeoError):
    """The requested object is not stored."""


class UnknownObject(NeoError):
    """A write refers to an object that is not stored."""


class AlreadyWatched(NeoError):
    """The object is already on the watchlist (the watchlist's business key)."""


class IdempotencyKeyReused(NeoError):
    """An idempotency key was sent again with a different request body."""
