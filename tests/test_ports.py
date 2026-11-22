"""Protocol conformance. These assignments are what pyright checks in CI.

At runtime they only prove the objects can be built; the real value is that
pyright fails the build if an adapter or a test fake drifts from ``ports.py``.
"""

from client import NeoWsClient
from database import AsteroidRepository
from domain import FeedBatch
from ingest import DryRunStore
from ports import AsteroidReader, AsteroidStore, FeedSource
from test_dashboard_data import StubFeed, StubReader
from test_ingest import FakeFeed, InMemoryStore


def test_feed_sources_conform() -> None:
    feeds: list[FeedSource] = [NeoWsClient("k"), FakeFeed(), StubFeed(FeedBatch())]
    assert all(f.max_window_days == 7 for f in feeds)


def test_stores_and_readers_conform(conn) -> None:
    stores: list[AsteroidStore] = [AsteroidRepository(conn), DryRunStore(), InMemoryStore()]
    readers: list[AsteroidReader] = [AsteroidRepository(conn), StubReader()]
    assert len(stores) == 3 and len(readers) == 2
