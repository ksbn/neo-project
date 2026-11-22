"""How the dashboard presents errors. The CLI's counterpart is ``pipeline.CLI_OUTCOMES``.

Kept out of ``app.py`` so it can be tested without Streamlit.
"""

from __future__ import annotations

from collections.abc import Callable

from errors import ConfigError, NeoError, StorageError, UpstreamAuthError, UpstreamError
from resilience import SourceCoolingDown

UI_MESSAGES: dict[type[NeoError], Callable[[NeoError], str]] = {
    # Driver messages can contain host, port and user names; keep them in the server log.
    StorageError: lambda _: "The database is unavailable or the query failed (details are in the server log).",
    ConfigError: str,
    UpstreamAuthError: lambda _: "NASA rejected the API key; check NASA_API_KEY in .env.",
    UpstreamError: str,
}


def describe(exc: NeoError) -> str:
    """Safe, user-facing text for ``exc``, resolved by the most specific mapped class."""
    if isinstance(exc, SourceCoolingDown):
        return f"{describe(exc.last_error)} Retrying in {exc.retry_in_s:.0f}s (or press Refresh)."
    for kind in type(exc).__mro__:
        if kind in UI_MESSAGES:
            return UI_MESSAGES[kind](exc)
    return "Unexpected error (details are in the server log)."
