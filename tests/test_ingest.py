"""IngestService (ingest.py) with in-memory fakes: no HTTP, no database, no monkeypatching."""

import logging
from collections.abc import Sequence
from datetime import date

import pytest

from conftest import make_feed, make_neo
from domain import FeedBatch, Observation, UpsertResult, latest
from errors import NeoError, StorageError, UpstreamUnavailable
from ingest import DryRunStore, IngestService, LogObserver, PipelineStats, WindowFailed, WindowReport
from models import parse_feed


class FakeFeed:
    """Serves one asteroid per requested day; fails on windows starting on a date in ``fail_on``."""

    max_window_days = 7

    def __init__(self, fail_on: frozenset[date] = frozenset()) -> None:
        self.fail_on = fail_on
        self.calls: list[tuple[date, date]] = []

    def fetch_records(self, start_date: date, end_date: date) -> FeedBatch:
        self.calls.append((start_date, end_date))
        if start_date in self.fail_on:
            raise UpstreamUnavailable("NASA is down")
        days = [date.fromordinal(d).isoformat() for d in range(start_date.toordinal(), end_date.toordinal() + 1)]
        return parse_feed(make_feed({d: [make_neo(d.replace("-", ""), d)] for d in days}))


class InMemoryStore:
    def __init__(self, fail: bool = False) -> None:
        self.rows: dict[str, Observation] = {}
        self.fail = fail

    def save(self, observations: Sequence[Observation]) -> UpsertResult:
        if self.fail:
            raise StorageError("disk full")
        newest = latest(observations)
        inserted = sum(o.neo_reference_id not in self.rows for o in newest)
        self.rows.update({o.neo_reference_id: o for o in newest})
        return UpsertResult(inserted=inserted, unchanged=len(newest) - inserted)

    def count(self) -> int:
        return len(self.rows)


class RecordingObserver:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.error: NeoError | None = None

    def run_started(self, stats: PipelineStats) -> None:
        self.events.append(f"started {stats.windows_total}")

    def window_done(self, report: WindowReport) -> None:
        self.events.append(f"window {report.start}")

    def run_finished(self, stats: PipelineStats, error: NeoError | None) -> None:
        self.events.append("finished")
        self.error = error


def test_windows_are_fetched_and_stored_with_totals():
    feed, store = FakeFeed(), InMemoryStore()
    stats = IngestService(feed, store).run(date(2026, 11, 1), date(2026, 11, 10))

    assert feed.calls == [(date(2026, 11, 1), date(2026, 11, 7)), (date(2026, 11, 8), date(2026, 11, 10))]
    assert store.count() == 10
    assert (stats.windows_total, stats.windows_done, stats.fetched, stats.inserted) == (2, 2, 10, 10)


def test_window_size_comes_from_the_feed():
    feed = FakeFeed()
    feed.max_window_days = 3
    IngestService(feed, InMemoryStore()).run(date(2026, 11, 1), date(2026, 11, 7))
    assert [len(range(s.toordinal(), e.toordinal() + 1)) for s, e in feed.calls] == [3, 3, 1]


def test_dry_run_store_writes_nothing():
    stats = IngestService(FakeFeed(), DryRunStore()).run(date(2026, 11, 1), date(2026, 11, 3))
    assert (stats.fetched, stats.inserted, stats.unchanged) == (3, 0, 3)


def test_failure_keeps_progress_and_wraps_the_typed_cause():
    store, stats, observer = InMemoryStore(), PipelineStats(), RecordingObserver()
    service = IngestService(FakeFeed(fail_on=frozenset({date(2026, 11, 8)})), store, [observer])
    with pytest.raises(WindowFailed) as exc:
        service.run(date(2026, 11, 1), date(2026, 11, 10), stats)

    assert isinstance(exc.value.cause, UpstreamUnavailable)
    assert exc.value.__cause__ is exc.value.cause
    assert (exc.value.window_start, exc.value.window_end) == (date(2026, 11, 8), date(2026, 11, 10))
    assert stats.windows_done == 1 and store.count() == 7
    assert observer.events == ["started 2", "window 2026-11-01", "finished"]
    assert observer.error is exc.value.cause


def test_storage_failure_is_wrapped_too():
    with pytest.raises(WindowFailed) as exc:
        IngestService(FakeFeed(), InMemoryStore(fail=True)).run(date(2026, 11, 1), date(2026, 11, 1))
    assert isinstance(exc.value.cause, StorageError)


def test_observers_see_every_window_and_a_clean_finish():
    observer = RecordingObserver()
    IngestService(FakeFeed(), InMemoryStore(), [observer]).run(date(2026, 11, 1), date(2026, 11, 8))
    assert observer.events == ["started 2", "window 2026-11-01", "window 2026-11-08", "finished"]
    assert observer.error is None


def test_log_observer_reports_data_quality_problems(caplog):
    class GappyFeed(FakeFeed):
        def fetch_records(self, start_date: date, end_date: date) -> FeedBatch:
            return parse_feed(make_feed({"2026-11-01": [make_neo("1"), make_neo("2", name=None)]}))

    with caplog.at_level(logging.INFO, logger="pipeline"):
        IngestService(GappyFeed(), InMemoryStore(), [LogObserver()]).run(date(2026, 11, 1), date(2026, 11, 2))

    assert "Skipped invalid asteroid 2" in caplog.text
    assert "no entry for 2026-11-02" in caplog.text
    assert "Done: 2 fetched" in caplog.text
