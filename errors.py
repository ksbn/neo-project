"""Exit codes and the typed error hierarchy shared by every module.

Rule of thumb: adapters (``client.py``, ``models.py``, ``database.py``) catch
their library's exceptions (``requests``, ``pydantic``, ``psycopg``) and
re-raise one of the :class:`NeoError` subclasses below, keeping the original as
``__cause__``. Entry points (``pipeline.py``, ``app.py``) catch only
``NeoError``; anything else is a bug and is allowed to crash with a traceback.
"""

from __future__ import annotations

from enum import IntEnum
from typing import ClassVar


class ExitCode(IntEnum):
    """Process exit codes of ``pipeline.py`` (6 is reserved for ``scripts/migrate.sh``)."""

    OK = 0
    CONFIG_ERROR = 1
    USAGE_ERROR = 2
    API_UNAVAILABLE = 3
    DATABASE_ERROR = 4
    PARTIAL = 5
    CONTRACT_ERROR = 7
    INTERRUPTED = 130


class NeoError(Exception):
    """Base for expected, operational failures. Raise a subclass, never this class."""

    exit_code: ClassVar[ExitCode]

    @property
    def user_message(self) -> str:
        """Text that is safe to show to a dashboard user."""
        return str(self)


class ConfigError(NeoError, ValueError):
    """Missing or invalid settings. Fix the configuration; retrying will not help."""

    exit_code = ExitCode.CONFIG_ERROR


class UpstreamError(NeoError):
    """The NASA NeoWs API could not be reached or returned an unusable response."""

    exit_code: ClassVar[ExitCode] = ExitCode.API_UNAVAILABLE
    retryable: ClassVar[bool] = False

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class UpstreamUnavailable(UpstreamError):
    """Network failure, timeout or HTTP 5xx after all retries. Try again later."""

    retryable = True


class UpstreamRateLimited(UpstreamError):
    """HTTP 429 after all retries. Try again later or use a personal API key."""

    retryable = True


class UpstreamAuthError(UpstreamError):
    """HTTP 401/403: the API key is missing, invalid or revoked."""

    exit_code = ExitCode.CONFIG_ERROR


class FeedContractError(UpstreamError):
    """NASA answered, but not in the documented format (schema drift, HTML error page)."""

    exit_code = ExitCode.CONTRACT_ERROR


class StorageError(NeoError):
    """PostgreSQL could not be reached, or a statement failed."""

    exit_code = ExitCode.DATABASE_ERROR

    @property
    def user_message(self) -> str:
        # Driver messages can contain host, port and user names; keep them in the logs.
        return "The database is unavailable or the query failed (details are in the server log)."
