"""Protocol conformance. These assignments are what pyright checks in CI.

At runtime they only prove the objects can be built; the real value is that
pyright fails the build if an adapter or a test fake drifts from ``ports.py``.
"""

from client import NeoWsClient
from database import AsteroidRepository, IngestionRunRepository
from domain import FeedBatch
from ingest import DryRunStore
from ports import AsteroidReader, AsteroidStore, FeedSource, RunLog, RunReader
from test_dashboard_data import StubFeed, StubReader, StubRuns
from test_ingest import FakeFeed, FakeRunLog, InMemoryStore
from test_pipeline import RunLogSpy


def test_feed_sources_conform() -> None:
    feeds: list[FeedSource] = [NeoWsClient("k"), FakeFeed(), StubFeed(FeedBatch())]
    assert all(f.max_window_days == 7 for f in feeds)


def test_stores_and_readers_conform(conn) -> None:
    stores: list[AsteroidStore] = [AsteroidRepository(conn), DryRunStore(), InMemoryStore()]
    readers: list[AsteroidReader] = [AsteroidRepository(conn), StubReader()]
    assert len(stores) == 3 and len(readers) == 2


def test_run_logs_conform(conn) -> None:
    logs: list[RunLog] = [IngestionRunRepository(conn), FakeRunLog(), RunLogSpy()]
    readers: list[RunReader] = [IngestionRunRepository(conn), StubRuns()]
    assert len(logs) == 3 and len(readers) == 2
