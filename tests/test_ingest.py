"""IngestService (ingest.py) with in-memory fakes: no HTTP, no database, no monkeypatching."""

from collections.abc import Sequence
from datetime import date

import pytest

from conftest import make_feed, make_neo
from database import UpsertResult
from domain import AsteroidRecord
from errors import StorageError, UpstreamUnavailable
from ingest import IngestService, PipelineStats, WindowFailed
from models import ParseResult, parse_feed


class FakeFeed:
    """Serves one asteroid per requested day; fails on the windows listed in ``fail_on``."""

    def __init__(self, fail_on: set[date] = frozenset()) -> None:
        self.fail_on = fail_on
        self.calls: list[tuple[date, date]] = []

    def fetch_records(self, start: date, end: date) -> ParseResult:
        self.calls.append((start, end))
        if start in self.fail_on:
            raise UpstreamUnavailable("NASA is down")
        days = [date.fromordinal(d).isoformat() for d in range(start.toordinal(), end.toordinal() + 1)]
        return parse_feed(make_feed({d: [make_neo(d.replace("-", ""), d)] for d in days}))


class InMemoryStore:
    def __init__(self, fail: bool = False) -> None:
        self.rows: dict[str, AsteroidRecord] = {}
        self.fail = fail

    def upsert_many(self, records: Sequence[AsteroidRecord]) -> UpsertResult:
        if self.fail:
            raise StorageError("disk full")
        inserted = sum(r.neo_reference_id not in self.rows for r in records)
        self.rows.update({r.neo_reference_id: r for r in records})
        return UpsertResult(inserted=inserted, unchanged=len(records) - inserted)

    def count(self) -> int:
        return len(self.rows)


def test_windows_are_fetched_and_stored_with_totals():
    feed, store = FakeFeed(), InMemoryStore()
    stats = IngestService(feed, store).run(date(2026, 11, 1), date(2026, 11, 10))

    assert feed.calls == [(date(2026, 11, 1), date(2026, 11, 7)), (date(2026, 11, 8), date(2026, 11, 10))]
    assert store.count() == 10
    assert (stats.windows_total, stats.windows_done, stats.fetched, stats.inserted) == (2, 2, 10, 10)


def test_dry_run_stores_nothing():
    stats = IngestService(FakeFeed(), None).run(date(2026, 11, 1), date(2026, 11, 3))
    assert (stats.fetched, stats.inserted) == (3, 0)


def test_failure_keeps_progress_and_wraps_the_typed_cause():
    store, stats = InMemoryStore(), PipelineStats()
    with pytest.raises(WindowFailed) as exc:
        IngestService(FakeFeed(fail_on={date(2026, 11, 8)}), store).run(date(2026, 11, 1), date(2026, 11, 10), stats)

    assert isinstance(exc.value.cause, UpstreamUnavailable)
    assert (exc.value.window_start, exc.value.window_end) == (date(2026, 11, 8), date(2026, 11, 10))
    assert stats.windows_done == 1 and store.count() == 7


def test_storage_failure_is_wrapped_too():
    with pytest.raises(WindowFailed) as exc:
        IngestService(FakeFeed(), InMemoryStore(fail=True)).run(date(2026, 11, 1), date(2026, 11, 1))
    assert isinstance(exc.value.cause, StorageError)
