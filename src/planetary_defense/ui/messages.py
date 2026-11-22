"""How the dashboard words errors and the pipeline's status. The CLI's counterpart is ``cli.CLI_OUTCOMES``.

Kept out of ``ui/app.py`` so it can be tested without Streamlit.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal

from planetary_defense.domain.errors import (
    ConfigError,
    NeoError,
    OrbitUnavailable,
    StorageError,
    UpstreamAuthError,
    UpstreamError,
)
from planetary_defense.domain.models import OrbitLookup, OrbitOrigin, PipelineStatus, RunStatus
from planetary_defense.infrastructure.error_boundary import SourceCoolingDown

UI_MESSAGES: dict[type[NeoError], Callable[[NeoError], str]] = {
    # Driver messages can contain host, port and user names; keep them in the server log.
    StorageError: lambda _: "The database is unavailable or the query failed (details are in the server log).",
    ConfigError: str,
    UpstreamAuthError: lambda _: "NASA rejected the API key; check NASA_API_KEY in .env.",
    UpstreamError: str,
    OrbitUnavailable: str,
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

# Stored runs keep the error's class name (domain/errors.py); this is how the dashboard words it.
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
        n = round(hours)
        return f"{n} hour{'s' if n != 1 else ''} ago"
    return f"{round(hours / 24)} days ago"


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


# --------------------------------------------------------------------------- #
# Orbit provenance
# --------------------------------------------------------------------------- #


def orbit_status(lookup: OrbitLookup, now: datetime) -> str:
    """Where the inspector's orbit came from, in one sentence (plus the lookup's own note)."""
    fetched = lookup.fetched_at.astimezone().strftime("%Y-%m-%d %H:%M")
    if lookup.origin is OrbitOrigin.NASA:
        text = "Orbit fetched from NASA just now."
    elif lookup.origin is OrbitOrigin.CACHE:
        text = f"Orbit from the local cache, fetched from NASA {_ago(now - lookup.fetched_at)} ({fetched})."
    else:
        text = f"Stale orbit from the local cache ({fetched})."
    return f"{text} {lookup.note}" if lookup.note else text
