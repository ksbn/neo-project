"""Protocol conformance. These assignments are what pyright checks in CI.

At runtime they only prove the objects can be built; the real value is that
pyright fails the build if an adapter or a test fake drifts from ``ports.py``.
"""

from planetary_defense.application.ingest_feed import DryRunStore
from planetary_defense.application.ports import (
    AlertStore,
    AsteroidReader,
    AsteroidStore,
    FeedSource,
    OrbitCache,
    OrbitSource,
    RunLog,
    RunReader,
)
from planetary_defense.domain.models import FeedBatch
from planetary_defense.infrastructure.nasa_client import NeoWsClient
from planetary_defense.infrastructure.repository import (
    AlertRepository,
    AsteroidRepository,
    IngestionRunRepository,
    OrbitRepository,
)

from fakes import (
    AlertStoreSpy,
    FakeFeed,
    FakeOrbitSource,
    FakeRunLog,
    InMemoryOrbitCache,
    InMemoryStore,
    RunLogSpy,
    StubFeed,
    StubReader,
    StubRuns,
)


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


def test_enrichment_ports_conform(conn) -> None:
    sources: list[OrbitSource] = [NeoWsClient("k"), FakeOrbitSource()]
    caches: list[OrbitCache] = [OrbitRepository(conn), InMemoryOrbitCache()]
    alert_stores: list[AlertStore] = [AlertRepository(conn), AlertStoreSpy()]
    assert len(sources) == len(caches) == len(alert_stores) == 2
