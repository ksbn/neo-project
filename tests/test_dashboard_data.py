"""Dashboard query service (dashboard_data.py): no Streamlit, pandas, HTTP or database."""

from datetime import date

import pytest

from conftest import make_feed, make_neo
from dashboard_data import FallbackReader, Source, view_from_feed, view_from_store
from domain import AsteroidSummary, AsteroidView, FeedBatch, Overview
from errors import ConfigError, NeoError, StorageError
from models import parse_feed
from ui_messages import describe


def view(*names: str) -> AsteroidView:
    rows = [AsteroidSummary(n, n, None, None, False, None, None) for n in names]
    return AsteroidView(Overview(total=len(rows), hazardous=0, latest_approach=None), rows)


def failing(exc: Exception):
    def read() -> AsteroidView:
        raise exc

    return read


# --------------------------------------------------------------------------- #
# FallbackReader
# --------------------------------------------------------------------------- #


def test_first_non_empty_source_wins_and_later_sources_are_not_called():
    second = Source("api", failing(AssertionError("must not be called")))
    result = FallbackReader(Source("db", lambda: view("a")), second).load()
    assert result.source == "db" and result.diagnostics == []
    assert result.view is not None and [r.name for r in result.view.rows] == ["a"]


def test_operational_failure_falls_back_with_the_entry_points_wording():
    result = FallbackReader(
        Source("db", failing(StorageError("connection to host 10.0.0.5 user neo_user refused"))),
        Source("api", lambda: view("b")),
        describe=describe,
    ).load()
    assert result.source == "api"
    (diagnostic,) = result.diagnostics
    assert diagnostic.startswith("db: The database is unavailable")
    assert "10.0.0.5" not in diagnostic and "neo_user" not in diagnostic


def test_empty_source_reports_its_hint_and_falls_back():
    result = FallbackReader(Source("db", lambda: view(), empty_hint="table is empty"), Source("api", lambda: view("c"))).load()
    assert result.source == "api"
    assert result.diagnostics == ["db: table is empty"]


def test_all_sources_failing_returns_no_view_with_every_reason():
    result = FallbackReader(
        Source("db", failing(ConfigError("Missing database settings: DB_PASSWORD"))),
        Source("api", failing(ConfigError("NASA_API_KEY is not set"))),
    ).load()
    assert result.view is None and result.source is None
    assert [d.split(":")[0] for d in result.diagnostics] == ["db", "api"]


def test_programmer_errors_propagate_instead_of_triggering_the_fallback():
    with pytest.raises(KeyError):
        FallbackReader(Source("db", failing(KeyError("bug"))), Source("api", lambda: view("x"))).load()


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #


class StubReader:
    def __init__(self) -> None:
        self.limits: list[int] = []

    def overview(self) -> Overview:
        return Overview(total=10_000, hazardous=42, latest_approach=date(2026, 11, 22))

    def list_recent(self, limit: int) -> list[AsteroidSummary]:
        self.limits.append(limit)
        return view("a").rows


def test_store_view_keeps_whole_table_totals_with_limited_rows():
    reader = StubReader()
    result = view_from_store(reader, limit=1)
    assert reader.limits == [1]
    assert result.overview.total == 10_000 and len(result.rows) == 1


class StubFeed:
    max_window_days = 7

    def __init__(self, batch: FeedBatch) -> None:
        self.batch = batch
        self.calls: list[tuple[date, date]] = []

    def fetch_records(self, start_date: date, end_date: date) -> FeedBatch:
        self.calls.append((start_date, end_date))
        return self.batch


def test_feed_view_covers_three_days_dedupes_and_sorts_newest_first():
    batch = parse_feed(
        make_feed(
            {
                "2026-11-08": [make_neo("old", "2026-11-08"), make_neo("twice", "2026-11-08")],
                "2026-11-10": [make_neo("new", "2026-11-10", is_potentially_hazardous_asteroid=True), make_neo("twice", "2026-11-10")],
            }
        )
    )
    feed = StubFeed(batch)
    result = view_from_feed(feed, today=date(2026, 11, 10))

    assert feed.calls == [(date(2026, 11, 8), date(2026, 11, 10))]
    assert [r.neo_reference_id for r in result.rows][:2] in (["twice", "new"], ["new", "twice"])
    assert result.rows[-1].neo_reference_id == "old"
    assert result.overview == Overview(total=3, hazardous=1, latest_approach=date(2026, 11, 10))


def test_every_error_class_has_safe_wording():
    def subclasses(kind: type[NeoError]):
        for sub in kind.__subclasses__():
            yield sub
            yield from subclasses(sub)

    for kind in subclasses(NeoError):
        if kind.__module__ == "errors":
            text = describe(kind("detail with secrets at 10.0.0.5"))
            assert text and isinstance(text, str)
