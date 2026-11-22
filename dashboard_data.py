"""Dashboard query service: builds an :class:`domain.AsteroidView` from whichever source works.

No Streamlit, pandas, psycopg or requests here. The sources are injected by
``app.py`` (the dashboard's composition root), and the fallback order is
data (:class:`FallbackReader` takes a list of :class:`Source`). Only
:class:`errors.NeoError` triggers the fallback; any other exception is a bug
and propagates.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Final

from domain import AsteroidSummary, AsteroidView, Overview, latest
from errors import NeoError
from ports import AsteroidReader, FeedSource, RunReader

logger = logging.getLogger(__name__)

MAX_ROWS: Final = 5_000
# Live fallback covers today and the 2 days before it, matching scripts/daily_pipeline.sh.
API_FALLBACK_DAYS: Final = 2


def view_from_store(reader: AsteroidReader, runs: RunReader, limit: int = MAX_ROWS) -> AsteroidView:
    """Totals over the whole table, the newest ``limit`` rows and the pipeline's status.

    The status is secondary: if only it fails, the asteroid data is still shown
    (``status=None``) rather than falling back to the live API.
    """
    overview, rows = reader.overview(), reader.list_recent(limit)
    try:
        status = runs.pipeline_status()
    except NeoError as exc:
        logger.warning("Pipeline status unavailable: %s", exc)
        status = None
    return AsteroidView(overview=overview, rows=rows, status=status, history_available=True)


def view_from_feed(feed: FeedSource, today: date, days: int = API_FALLBACK_DAYS) -> AsteroidView:
    """The last few days live from a feed, shaped like the stored view."""
    batch = feed.fetch_records(today - timedelta(days=days), today)
    rows = [
        AsteroidSummary(
            neo_reference_id=obs.neo_reference_id,
            name=obs.asteroid.name,
            estimated_diameter_min_km=_float(obs.asteroid.estimated_diameter_min_km),
            estimated_diameter_max_km=_float(obs.asteroid.estimated_diameter_max_km),
            is_potentially_hazardous=obs.asteroid.is_potentially_hazardous,
            close_approach_date=obs.approach_date,
            miss_distance_km=_float(obs.approach.miss_distance_km) if obs.approach else None,
        )
        for obs in latest(batch.observations)
    ]
    rows.sort(key=lambda r: (r.close_approach_date is not None, r.close_approach_date or date.min), reverse=True)
    dates = [r.close_approach_date for r in rows if r.close_approach_date is not None]
    passes = {(o.neo_reference_id, o.approach_date, o.approach.orbiting_body) for o in batch.observations if o.approach}
    overview = Overview(
        total=len(rows),
        hazardous=sum(r.is_potentially_hazardous for r in rows),
        latest_approach=max(dates, default=None),
        approaches=len(passes),
    )
    return AsteroidView(overview=overview, rows=rows)


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
    owned by the entry point (see ``ui_messages.describe``).
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
