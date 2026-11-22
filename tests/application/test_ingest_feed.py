"""IngestService (application/ingest_feed.py) with in-memory fakes: no HTTP, no database, no monkeypatching."""

import logging
from datetime import UTC, date, datetime, timedelta

import pytest

from planetary_defense.application.ingest_feed import (
    AlertRecorder,
    DryRunStore,
    IngestionRunRecorder,
    IngestService,
    LogObserver,
    PipelineStats,
    WindowFailed,
    WindowReport,
)
from planetary_defense.domain.anomaly_rules import high_velocity
from planetary_defense.domain.errors import NeoError, StorageError, UpstreamUnavailable
from planetary_defense.domain.models import FeedBatch, RunStatus
from planetary_defense.infrastructure.nasa_dto import parse_feed

from fakes import AlertStoreSpy, FakeFeed, FakeRunLog, InMemoryStore
from support import make_feed, make_neo


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


# --------------------------------------------------------------------------- #
# IngestionRunRecorder
# --------------------------------------------------------------------------- #


class TickingClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 22, 8, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=30)
        return self.now


def run_with_recorder(feed: FakeFeed, end: date, log: FakeRunLog) -> None:
    recorder = IngestionRunRecorder(log, clock=TickingClock())
    IngestService(feed, InMemoryStore(), [recorder]).run(date(2026, 11, 1), end)


def test_recorder_writes_a_success_with_totals():
    log = FakeRunLog()
    run_with_recorder(FakeFeed(), date(2026, 11, 8), log)

    (run,) = log.runs
    totals = (run.status, run.windows_total, run.windows_done, run.inserted, run.new_approaches)
    assert totals == (RunStatus.SUCCESS, 2, 2, 8, 8)
    assert (run.start_date, run.end_date) == (date(2026, 11, 1), date(2026, 11, 8))
    assert run.finished_at - run.started_at == timedelta(seconds=30)
    assert run.error_category is None and run.error_message is None


def test_recorder_tells_partial_from_failed():
    partial, failed = FakeRunLog(), FakeRunLog()
    with pytest.raises(WindowFailed):
        run_with_recorder(FakeFeed(fail_on=frozenset({date(2026, 11, 8)})), date(2026, 11, 8), partial)
    with pytest.raises(WindowFailed):
        run_with_recorder(FakeFeed(fail_on=frozenset({date(2026, 11, 1)})), date(2026, 11, 8), failed)

    assert (partial.runs[0].status, partial.runs[0].windows_done) == (RunStatus.PARTIAL, 1)
    assert (failed.runs[0].status, failed.runs[0].windows_done) == (RunStatus.FAILED, 0)
    assert failed.runs[0].error_category == "UpstreamUnavailable"
    assert failed.runs[0].error_message == "NASA is down"


def test_recorder_failure_does_not_change_the_run(caplog):
    """Recording is best effort: the data was saved, so the run still succeeds."""
    stats = IngestService(FakeFeed(), InMemoryStore(), [IngestionRunRecorder(FakeRunLog(fail=True))]).run(
        date(2026, 11, 1), date(2026, 11, 1)
    )
    assert stats.windows_done == 1
    assert "Could not record the pipeline run" in caplog.text


# --------------------------------------------------------------------------- #
# AlertRecorder
# --------------------------------------------------------------------------- #


def test_alert_recorder_scores_each_window_and_replaces_its_alerts():
    spy = AlertStoreSpy()
    recorder = AlertRecorder([high_velocity(10.0)], spy)  # test NEOs fly at 12.3 km/s
    IngestService(FakeFeed(), InMemoryStore(), [recorder]).run(date(2026, 11, 1), date(2026, 11, 8))

    assert len(spy.calls) == 2  # one replace per window
    passes, alerts = spy.calls[0]
    assert passes == [(f"2026110{d}", date(2026, 11, d)) for d in range(1, 8)]
    assert [(a.neo_reference_id, a.rule) for a in alerts] == [(ref, "high_velocity") for ref, _ in passes]
    assert recorder.raised == 8


def test_alert_recorder_is_best_effort(caplog):
    recorder = AlertRecorder([high_velocity(10.0)], AlertStoreSpy(fail=StorageError("disk full")))
    stats = IngestService(FakeFeed(), InMemoryStore(), [recorder]).run(date(2026, 11, 1), date(2026, 11, 1))
    assert stats.windows_done == 1
    assert "Could not save alerts" in caplog.text
