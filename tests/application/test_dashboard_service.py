"""Dashboard query service (application/dashboard_service.py): no Streamlit, pandas, HTTP or database."""

from datetime import date

import pytest

from planetary_defense.application.dashboard_service import (
    FallbackReader,
    Source,
    threat_level,
    view_from_feed,
    view_from_store,
    window_summary,
)
from planetary_defense.domain.errors import ConfigError, NeoError, StorageError
from planetary_defense.domain.models import (
    AnomalyAlert,
    AsteroidSummary,
    AsteroidView,
    PipelineStatus,
    Severity,
)
from planetary_defense.domain.units import LUNAR_DISTANCE_KM
from planetary_defense.infrastructure.nasa_dto import parse_feed
from planetary_defense.ui.messages import describe

from fakes import LAST_RUN, StubAlerts, StubFeed, StubReader, StubRuns, view
from support import make_feed, make_neo


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
    result = FallbackReader(
        Source("db", lambda: view(), empty_hint="table is empty"), Source("api", lambda: view("c"))
    ).load()
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


def test_store_view_keeps_whole_table_totals_with_limited_rows():
    reader = StubReader()
    result = view_from_store(reader, StubRuns(), StubAlerts(), limit=1)
    assert reader.limits == [1]
    assert result.overview.total == 10_000 and len(result.rows) == 1
    assert result.history_available
    assert result.status == PipelineStatus(last_run=LAST_RUN, last_success_at=LAST_RUN.finished_at)


def test_store_view_survives_a_status_failure():
    """Run status is secondary: losing it must not hide the asteroid data."""
    result = view_from_store(StubReader(), StubRuns(fail=True), StubAlerts())
    assert result.status is None and result.overview.total == 10_000


def test_feed_view_covers_three_days_dedupes_and_sorts_newest_first():
    batch = parse_feed(
        make_feed(
            {
                "2026-11-08": [make_neo("old", "2026-11-08"), make_neo("twice", "2026-11-08")],
                "2026-11-10": [
                    make_neo("new", "2026-11-10", is_potentially_hazardous_asteroid=True),
                    make_neo("twice", "2026-11-10"),
                ],
            }
        )
    )
    feed = StubFeed(batch)
    result = view_from_feed(feed, today=date(2026, 11, 10))

    assert feed.calls == [(date(2026, 11, 8), date(2026, 11, 10))]
    assert [r.neo_reference_id for r in result.rows][:2] in (["twice", "new"], ["new", "twice"])
    assert result.rows[-1].neo_reference_id == "old"
    assert (result.overview.total, result.overview.hazardous, result.overview.approaches) == (3, 1, 4)
    assert result.overview.latest_approach == date(2026, 11, 10)
    assert result.overview.closest_miss_ld == pytest.approx(4567890.123 / LUNAR_DISTANCE_KM)
    assert result.overview.fastest_km_s == pytest.approx(12.345678)
    assert result.rows[0].relative_velocity_km_s == pytest.approx(12.345678)
    assert result.status is None and not result.history_available


def test_every_error_class_has_safe_wording():
    def subclasses(kind: type[NeoError]):
        for sub in kind.__subclasses__():
            yield sub
            yield from subclasses(sub)

    for kind in subclasses(NeoError):
        if kind.__module__ == NeoError.__module__:
            text = describe(kind("detail with secrets at 10.0.0.5"))
            assert text and isinstance(text, str)


ALERT = AnomalyAlert("close_approach", Severity.WARNING, "a", "(a)", date(2026, 11, 10), "Passes 3.00 LD", 3.0, 5.0)


def test_store_view_includes_stored_alerts():
    result = view_from_store(StubReader(), StubRuns(), StubAlerts([ALERT]))
    assert result.alerts == [ALERT]


def test_store_view_survives_an_alerts_failure():
    """E.g. before the anomaly_alerts migration has run: the asteroid data is still shown."""
    result = view_from_store(StubReader(), StubRuns(), StubAlerts(fail=True))
    assert result.alerts == [] and result.overview.total == 10_000 and result.status is not None


def test_feed_view_scores_alerts_live():
    near = make_neo("near", "2026-11-10")
    near["close_approach_data"][0]["miss_distance"]["kilometers"] = str(3 * LUNAR_DISTANCE_KM)
    batch = parse_feed(make_feed({"2026-11-10": [near, make_neo("far", "2026-11-10")]}))

    result = view_from_feed(StubFeed(batch), today=date(2026, 11, 10))
    assert [(a.neo_reference_id, a.rule, a.severity) for a in result.alerts] == [
        ("near", "close_approach", Severity.WARNING),
    ]
    assert result.overview.closest_miss_ld == pytest.approx(3.0)


# --------------------------------------------------------------------------- #
# KPI strip figures
# --------------------------------------------------------------------------- #

TODAY = date(2026, 11, 22)


def summary(ref: str, day: date | None, km: float | None) -> AsteroidSummary:
    return AsteroidSummary(ref, f"({ref})", None, None, False, day, km)


def test_window_summary_counts_passes_near_today_and_finds_the_closest():
    rows = [summary("in-far", date(2026, 11, 20), 9e6), summary("in-near", date(2026, 11, 25), 2e6),
            summary("too-old", date(2026, 11, 18), 1e5), summary("no-date", None, 1e5)]
    w = window_summary(rows, TODAY, days=3)
    assert (w.start, w.end, w.count) == (date(2026, 11, 19), date(2026, 11, 25), 2)
    assert w.closest is not None and w.closest.neo_reference_id == "in-near"
    assert w.closest_ld == pytest.approx(2e6 / LUNAR_DISTANCE_KM)
    assert window_summary([], TODAY).closest is None


def test_threat_level_is_the_worst_alert_near_today():
    def alert(day: date, severity: Severity) -> AnomalyAlert:
        return AnomalyAlert("r", severity, "x", "(x)", day, "m")

    assert threat_level([], TODAY) is None
    alerts = [alert(date(2026, 11, 1), Severity.CRITICAL), alert(date(2026, 11, 20), Severity.WATCH),
              alert(date(2026, 11, 28), Severity.WARNING)]
    assert threat_level(alerts, TODAY) is Severity.WARNING  # the November 1 critical is outside +-7 days
