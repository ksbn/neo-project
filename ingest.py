"""Application service: fetch, validate and store asteroids window by window.

Knows nothing about HTTP, SQL, pydantic, ``.env`` or exit codes. Its
collaborators are injected (see ``ports.py``), and every expected failure it
sees is a :class:`errors.NeoError`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import date, timedelta

from client import date_chunks
from errors import NeoError
from ports import AsteroidStore, FeedSource

logger = logging.getLogger("pipeline")


@dataclass(frozen=True)
class WindowResult:
    """What happened in one window."""

    fetched: int = 0
    invalid: int = 0
    unique: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    missing_dates: int = 0


@dataclass
class PipelineStats:
    """Running totals across all fetched windows."""

    windows_total: int = 0
    windows_done: int = 0
    fetched: int = 0
    invalid: int = 0
    unique: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    missing_dates: int = 0

    def add(self, window: WindowResult) -> None:
        self.windows_done += 1
        self.fetched += window.fetched
        self.invalid += window.invalid
        self.unique += window.unique
        self.inserted += window.inserted
        self.updated += window.updated
        self.unchanged += window.unchanged
        self.missing_dates += window.missing_dates


class WindowFailed(Exception):
    """A single 7-day window failed; earlier windows may already be committed.

    Always raised ``from`` the underlying :class:`NeoError`, available as :attr:`cause`.
    """

    def __init__(self, window_start: date, window_end: date, cause: NeoError) -> None:
        super().__init__(f"window {window_start}..{window_end} failed: {cause}")
        self.window_start = window_start
        self.window_end = window_end

    @property
    def cause(self) -> NeoError:
        assert isinstance(self.__cause__, NeoError)
        return self.__cause__


class IngestService:
    """Runs the pipeline over any date range. ``store=None`` means dry run."""

    def __init__(self, feed: FeedSource, store: AsteroidStore | None) -> None:
        self._feed = feed
        self._store = store

    def run(self, start_date: date, end_date: date, stats: PipelineStats | None = None) -> PipelineStats:
        """Fetch, validate and store the inclusive range ``start_date``..``end_date``.

        Pass ``stats`` to observe progress even if the run fails part-way.

        Raises:
            ValueError: ``end_date`` is before ``start_date`` (a caller bug; the CLI validates first).
            WindowFailed: A window failed with a NeoError; earlier windows stay committed.
        """
        stats = stats if stats is not None else PipelineStats()
        windows = list(date_chunks(start_date, end_date))
        stats.windows_total = len(windows)
        logger.info(
            "Starting pipeline for %s to %s (%d window%s)%s",
            start_date, end_date, len(windows), "" if len(windows) == 1 else "s",
            " (dry run)" if self._store is None else "",
        )

        for window_start, window_end in windows:
            try:
                result = self._process_window(window_start, window_end)
            except NeoError as exc:
                raise WindowFailed(window_start, window_end, exc) from exc
            stats.add(result)

        if self._store is not None:
            logger.info("Table now holds %d asteroids", self._store.count())
        return stats

    def _process_window(self, window_start: date, window_end: date) -> WindowResult:
        """Fetch, validate and (unless dry run) upsert one window."""
        result = self._feed.fetch_records(window_start, window_end)

        for bad in result.invalid:
            logger.warning(
                "Skipped invalid asteroid %s on %s: %s",
                bad.neo_reference_id or "<unknown id>", bad.feed_date, bad.error,
            )

        missing = _missing_dates(window_start, window_end, result.feed_dates)
        if missing:
            logger.warning(
                "NeoWs returned no entry for %s; data for those days may be incomplete",
                ", ".join(d.isoformat() for d in missing),
            )

        window = WindowResult(
            fetched=result.element_count,
            invalid=len(result.invalid),
            unique=len(result.records),
            missing_dates=len(missing),
        )

        if self._store is None:
            logger.info(
                "%s..%s: %d objects, %d valid unique asteroids (not written: dry run)",
                window_start, window_end, result.element_count, len(result.records),
            )
            return window

        upsert = self._store.upsert_many(result.records)
        logger.info(
            "%s..%s: %d objects -> %d inserted, %d updated, %d unchanged, %d invalid",
            window_start, window_end, result.element_count,
            upsert.inserted, upsert.updated, upsert.unchanged, len(result.invalid),
        )
        return replace(window, inserted=upsert.inserted, updated=upsert.updated, unchanged=upsert.unchanged)


def _missing_dates(start: date, end: date, present: frozenset[date]) -> list[date]:
    requested = {start + timedelta(days=i) for i in range((end - start).days + 1)}
    return sorted(requested - present)
