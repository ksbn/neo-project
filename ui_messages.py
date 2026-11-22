"""How the dashboard words errors and the pipeline's status. The CLI's counterpart is ``pipeline.CLI_OUTCOMES``.

Kept out of ``app.py`` so it can be tested without Streamlit.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal

from domain import PipelineStatus, RunStatus
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


# --------------------------------------------------------------------------- #
# Pipeline status
# --------------------------------------------------------------------------- #

# Data older than this is flagged; the daily job refreshes it about every 24 hours.
STALE_AFTER: Final = timedelta(hours=36)

# Stored runs keep the error's class name (errors.py); this is how the dashboard words it.
_RUN_FAILURE_LABELS: Final = {
    "ConfigError": "a configuration problem",
    "UpstreamAuthError": "NASA rejecting the API key",
    "UpstreamRateLimited": "NASA's rate limit",
    "UpstreamUnavailable": "NASA being unreachable",
    "UpstreamRequestError": "NASA rejecting the request",
    "FeedContractError": "an unexpected NASA response format",
    "StorageError": "a database error",
}


@dataclass(frozen=True)
class Health:
    """What to show about the pipeline. ``level`` is a Streamlit alert kind."""

    level: Literal["success", "info", "warning", "error"]
    text: str


def _ago(delta: timedelta) -> str:
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return "less than an hour ago"
    if hours < 48:
        return f"{hours:.0f} hours ago"
    return f"{hours / 24:.0f} days ago"


def run_health(status: PipelineStatus | None, now: datetime, stale_after: timedelta = STALE_AFTER) -> Health:
    """Summarise the last pipeline run for the dashboard. ``now`` must be timezone-aware."""
    if status is None:
        return Health("info", "Pipeline status is unavailable.")
    run = status.last_run
    if run is None:
        return Health("info", "No pipeline runs recorded yet. They are recorded from the next run on.")

    when = run.finished_at.astimezone().strftime("%Y-%m-%d %H:%M")
    if run.status is not RunStatus.SUCCESS:
        cause = _RUN_FAILURE_LABELS.get(run.error_category or "", "an unexpected error")
        if run.status is RunStatus.PARTIAL:
            text = f"The last pipeline run ({when}) stopped part-way because of {cause}; some days were saved."
        else:
            text = f"The last pipeline run ({when}) failed because of {cause}; nothing was saved."
        if status.last_success_at:
            text += f" Last successful run: {_ago(now - status.last_success_at)}."
        return Health("error", text)

    age = now - run.finished_at
    summary = f"{run.inserted} new, {run.updated} updated asteroids, {run.new_approaches} new approaches"
    if age > stale_after:
        return Health("warning", f"Data may be stale: the last pipeline run finished {_ago(age)} ({when}; {summary}).")
    return Health("success", f"Last pipeline run: {when} ({_ago(age)}): {summary}.")
