"""Application service: fetch, validate and store asteroids window by window.

Imports only ``domain``, ``errors`` and ``ports``: nothing about HTTP, SQL,
pydantic, ``.env`` or exit codes. Collaborators are composed in:

* a :class:`ports.FeedSource` and an :class:`ports.AsteroidStore`
  (:class:`DryRunStore` for ``--dry-run``), and
* any number of :class:`RunObserver`s, which is how logging (:class:`LogObserver`)
  and run recording (:class:`IngestionRunRecorder`) plug in without changing the service.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from domain import (
    FeedBatch,
    IngestionRun,
    InvalidObject,
    Observation,
    RunStatus,
    UpsertResult,
    date_windows,
    latest,
)
from errors import NeoError, StorageError
from ports import AsteroidStore, FeedSource, RunLog

logger = logging.getLogger("pipeline")


@dataclass(frozen=True)
class WindowReport:
    """What happened in one window."""

    start: date
    end: date
    fetched: int
    unique: int
    invalid: list[InvalidObject] = field(default_factory=list)
    missing_dates: list[date] = field(default_factory=list)
    upsert: UpsertResult = field(default_factory=UpsertResult)


@dataclass
class PipelineStats:
    """Running totals across all windows of one run."""

    start: date | None = None
    end: date | None = None
    windows_total: int = 0
    windows_done: int = 0
    fetched: int = 0
    invalid: int = 0
    unique: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    missing_dates: int = 0
    new_approaches: int = 0

    def add(self, window: WindowReport) -> None:
        self.windows_done += 1
        self.fetched += window.fetched
        self.invalid += len(window.invalid)
        self.unique += window.unique
        self.inserted += window.upsert.inserted
        self.updated += window.upsert.updated
        self.unchanged += window.upsert.unchanged
        self.missing_dates += len(window.missing_dates)
        self.new_approaches += window.upsert.new_approaches


class WindowFailed(Exception):
    """A single window failed; earlier windows may already be committed."""

    def __init__(self, window_start: date, window_end: date, cause: NeoError) -> None:
        super().__init__(f"window {window_start}..{window_end} failed: {cause}")
        self.window_start = window_start
        self.window_end = window_end
        self.cause = cause


class RunObserver(Protocol):
    """Gets told what a run does. Observers must not raise."""

    def run_started(self, stats: PipelineStats) -> None: ...

    def window_done(self, report: WindowReport) -> None: ...

    def run_finished(self, stats: PipelineStats, error: NeoError | None) -> None: ...


class DryRunStore:
    """An :class:`ports.AsteroidStore` that writes nothing (Null Object for ``--dry-run``)."""

    def save(self, observations: Sequence[Observation]) -> UpsertResult:
        return UpsertResult(unchanged=len(latest(observations)))

    def count(self) -> int:
        return 0


class IngestService:
    """Runs the pipeline over any date range."""

    def __init__(self, feed: FeedSource, store: AsteroidStore, observers: Iterable[RunObserver] = ()) -> None:
        self._feed = feed
        self._store = store
        self._observers = tuple(observers)

    def run(self, start_date: date, end_date: date, stats: PipelineStats | None = None) -> PipelineStats:
        """Fetch, validate and store the inclusive range ``start_date``..``end_date``.

        Pass ``stats`` to observe progress even if the run fails part-way.

        Raises:
            ValueError: ``end_date`` is before ``start_date`` (a caller bug; the CLI validates first).
            WindowFailed: A window failed with a NeoError; earlier windows stay committed.
        """
        stats = stats if stats is not None else PipelineStats()
        windows = list(date_windows(start_date, end_date, self._feed.max_window_days))
        stats.start, stats.end, stats.windows_total = start_date, end_date, len(windows)
        for observer in self._observers:
            observer.run_started(stats)

        for window_start, window_end in windows:
            try:
                report = self._process_window(window_start, window_end)
            except NeoError as exc:
                self._finished(stats, exc)
                raise WindowFailed(window_start, window_end, exc) from exc
            stats.add(report)
            for observer in self._observers:
                observer.window_done(report)

        self._finished(stats, None)
        return stats

    def _process_window(self, window_start: date, window_end: date) -> WindowReport:
        batch: FeedBatch = self._feed.fetch_records(window_start, window_end)
        upsert = self._store.save(batch.observations)
        return WindowReport(
            start=window_start,
            end=window_end,
            fetched=batch.element_count,
            unique=batch.unique_asteroids,
            invalid=batch.invalid,
            missing_dates=_missing_dates(window_start, window_end, batch.feed_dates),
            upsert=upsert,
        )

    def _finished(self, stats: PipelineStats, error: NeoError | None) -> None:
        for observer in self._observers:
            observer.run_finished(stats, error)


class LogObserver:
    """Logs progress and data-quality warnings (the pipeline's console output)."""

    def __init__(self, log: logging.Logger = logger, *, dry_run: bool = False) -> None:
        self._log = log
        self._dry_run = dry_run

    def run_started(self, stats: PipelineStats) -> None:
        self._log.info(
            "Starting pipeline for %s to %s (%d window%s)%s",
            stats.start, stats.end, stats.windows_total, "" if stats.windows_total == 1 else "s",
            " (dry run)" if self._dry_run else "",
        )

    def window_done(self, report: WindowReport) -> None:
        for bad in report.invalid:
            self._log.warning(
                "Skipped invalid asteroid %s on %s: %s", bad.neo_reference_id or "<unknown id>", bad.feed_date, bad.error
            )
        if report.missing_dates:
            self._log.warning(
                "NeoWs returned no entry for %s; data for those days may be incomplete",
                ", ".join(d.isoformat() for d in report.missing_dates),
            )
        if self._dry_run:
            self._log.info(
                "%s..%s: %d objects, %d valid unique asteroids (not written: dry run)",
                report.start, report.end, report.fetched, report.unique,
            )
        else:
            self._log.info(
                "%s..%s: %d objects -> %d inserted, %d updated, %d unchanged, %d invalid, %d new approaches",
                report.start, report.end, report.fetched,
                report.upsert.inserted, report.upsert.updated, report.upsert.unchanged, len(report.invalid),
                report.upsert.new_approaches,
            )

    def run_finished(self, stats: PipelineStats, error: NeoError | None) -> None:
        if error is not None:
            return  # the entry point logs failures with their mapped label
        self._log.info(
            "Done: %d fetched, %d unique, %d inserted, %d updated, %d unchanged, %d invalid, %d missing days, "
            "%d new approaches",
            stats.fetched, stats.unique, stats.inserted, stats.updated, stats.unchanged, stats.invalid,
            stats.missing_dates, stats.new_approaches,
        )


class IngestionRunRecorder:
    """Writes one :class:`domain.IngestionRun` per run to a :class:`ports.RunLog`.

    Recording is best effort: if the log can't be written (database gone), the
    run's own outcome is unchanged and a warning is logged.
    """

    def __init__(self, log: RunLog, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._log = log
        self._clock = clock
        self._started_at: datetime | None = None

    def run_started(self, stats: PipelineStats) -> None:
        self._started_at = self._clock()

    def window_done(self, report: WindowReport) -> None:
        pass

    def run_finished(self, stats: PipelineStats, error: NeoError | None) -> None:
        finished_at = self._clock()
        if error is None:
            status = RunStatus.SUCCESS
        else:
            status = RunStatus.PARTIAL if stats.windows_done else RunStatus.FAILED
        if stats.start is None or stats.end is None:  # IngestService.run always sets the range first
            logger.warning("Not recording a run without a date range")
            return
        run = IngestionRun(
            started_at=self._started_at or finished_at,
            finished_at=finished_at,
            start_date=stats.start,
            end_date=stats.end,
            status=status,
            windows_total=stats.windows_total,
            windows_done=stats.windows_done,
            fetched=stats.fetched,
            inserted=stats.inserted,
            updated=stats.updated,
            unchanged=stats.unchanged,
            invalid=stats.invalid,
            missing_dates=stats.missing_dates,
            new_approaches=stats.new_approaches,
            error_category=type(error).__name__ if error is not None else None,
            error_message=str(error) if error is not None else None,
        )
        try:
            self._log.record(run)
        except StorageError as exc:
            logger.warning("Could not record the pipeline run in ingestion_runs: %s", exc)


def _missing_dates(start: date, end: date, present: frozenset[date]) -> list[date]:
    requested = {start + timedelta(days=i) for i in range((end - start).days + 1)}
    return sorted(requested - present)
