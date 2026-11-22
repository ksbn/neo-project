"""Dashboard query facade: KPIs, alerts, table rows and the selected asteroid, from whichever source works.

No Streamlit, pandas, psycopg or requests here. The sources are injected by
``ui/app.py`` (the dashboard's composition root), and the fallback order is
data (:class:`FallbackReader` takes a list of :class:`Source`). Only
:class:`domain.errors.NeoError` triggers the fallback; any other exception is a bug
and propagates.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Final, Protocol

from planetary_defense.application.ports import AlertReader, AsteroidReader, FeedSource, RunReader
from planetary_defense.domain.anomaly_rules import Rule, default_rules, evaluate
from planetary_defense.domain.errors import NeoError
from planetary_defense.domain.models import (
    ApproachSummary,
    AsteroidSummary,
    AsteroidView,
    OrbitLookup,
    Overview,
    Severity,
    latest,
)
from planetary_defense.domain.units import km_to_ld

logger = logging.getLogger(__name__)

MAX_ROWS: Final = 5_000
MAX_ALERTS: Final = 200
# Live fallback covers today and the 2 days before it, matching scripts/daily_pipeline.sh.
API_FALLBACK_DAYS: Final = 2


def view_from_store(
    reader: AsteroidReader, runs: RunReader, alerts: AlertReader, limit: int = MAX_ROWS, alert_limit: int = MAX_ALERTS
) -> AsteroidView:
    """Totals over the whole table, the newest ``limit`` rows, the pipeline's status and the stored alerts.

    Status and alerts are secondary: if only they fail (for example before their
    migrations ran), the asteroid data is still shown rather than falling back
    to the live API.
    """
    overview, rows = reader.overview(), reader.list_recent(limit)
    try:
        status = runs.pipeline_status()
    except NeoError as exc:
        logger.warning("Pipeline status unavailable: %s", exc)
        status = None
    try:
        stored_alerts = alerts.recent_alerts(Severity.INFO, alert_limit)
    except NeoError as exc:
        logger.warning("Alerts unavailable: %s", exc)
        stored_alerts = []
    return AsteroidView(overview=overview, rows=rows, status=status, history_available=True, alerts=stored_alerts)


def view_from_feed(
    feed: FeedSource, today: date, days: int = API_FALLBACK_DAYS, rules: Iterable[Rule] | None = None
) -> AsteroidView:
    """The last few days live from a feed, shaped like the stored view; alerts are scored on the fly."""
    batch = feed.fetch_records(today - timedelta(days=days), today)
    newest = latest(batch.observations)
    rows = [
        AsteroidSummary(
            neo_reference_id=obs.neo_reference_id,
            name=obs.asteroid.name,
            estimated_diameter_min_km=_float(obs.asteroid.estimated_diameter_min_km),
            estimated_diameter_max_km=_float(obs.asteroid.estimated_diameter_max_km),
            is_potentially_hazardous=obs.asteroid.is_potentially_hazardous,
            close_approach_date=obs.approach_date,
            miss_distance_km=_float(obs.approach.miss_distance_km) if obs.approach else None,
            relative_velocity_km_s=_float(obs.approach.relative_velocity_km_s) if obs.approach else None,
        )
        for obs in newest
    ]
    rows.sort(key=lambda r: (r.close_approach_date is not None, r.close_approach_date or date.min), reverse=True)
    dates = [r.close_approach_date for r in rows if r.close_approach_date is not None]
    passes = {(o.neo_reference_id, o.approach_date, o.approach.orbiting_body) for o in batch.observations if o.approach}
    misses = [km_to_ld(float(o.approach.miss_distance_km)) for o in batch.observations if o.approach]
    speeds = [float(o.approach.relative_velocity_km_s) for o in batch.observations if o.approach]
    overview = Overview(
        total=len(rows),
        hazardous=sum(r.is_potentially_hazardous for r in rows),
        latest_approach=max(dates, default=None),
        approaches=len(passes),
        closest_miss_ld=min(misses, default=None),
        fastest_km_s=max(speeds, default=None),
    )
    alerts = evaluate(newest, rules if rules is not None else default_rules())
    return AsteroidView(overview=overview, rows=rows, alerts=alerts)


def _float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


@dataclass(frozen=True)
class Source:
    """One place the dashboard can read from."""

    label: str
    read: Callable[[], AsteroidView]
    empty_hint: str = "returned no asteroids"


@dataclass(frozen=True)
class LoadResult:
    view: AsteroidView | None
    source: str | None
    diagnostics: list[str] = field(default_factory=list)


class FallbackReader:
    """Try each source in order; return the first non-empty view.

    ``describe`` turns an error into user-facing text; the presentation is
    owned by the entry point (see ``ui.messages.describe``).
    """

    def __init__(self, *sources: Source, describe: Callable[[NeoError], str] = str) -> None:
        self._sources = sources
        self._describe = describe

    def load(self) -> LoadResult:
        diagnostics: list[str] = []
        for src in self._sources:
            try:
                view = src.read()
            except NeoError as exc:
                logger.warning("%s failed: %s", src.label, exc)  # full detail stays in the server log
                diagnostics.append(f"{src.label}: {self._describe(exc)}")
                continue
            if not view.is_empty:
                return LoadResult(view, src.label, diagnostics)
            diagnostics.append(f"{src.label}: {src.empty_hint}")
        return LoadResult(None, None, diagnostics)


class DashboardData(Protocol):
    """Everything the Control Room page reads. ``ui/app.py`` implements it with caches and breakers;
    tests use an in-memory fake, so the page can be rendered without a database or NASA."""

    def load(self) -> LoadResult: ...

    def approach_history(self, neo_reference_id: str) -> list[ApproachSummary]:
        """Raises NeoError when the history can't be read."""
        ...

    def orbit(self, neo_reference_id: str) -> OrbitLookup:
        """Raises NeoError (usually OrbitUnavailable) when no orbit can be shown."""
        ...

    def refresh(self) -> None: ...

    def sync(self) -> SyncReport:
        """Fetch the latest days from NASA into the database now. Never raises for operational failures."""
        ...


@dataclass(frozen=True)
class SyncReport:
    ok: bool
    message: str
