"""Dashboard data access (dashboard_data.py), without Streamlit."""

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest
import responses
from responses import matchers

from client import BASE_URL
from config import Settings
from conftest import make_feed, make_neo
from dashboard_data import COLUMNS, FallbackReader, Source, normalize, read_nasa
from errors import ConfigError, StorageError

FEED = f"{BASE_URL}/feed"


def frame(*names: str) -> pd.DataFrame:
    return normalize(pd.DataFrame([{"neo_reference_id": n, "name": n} for n in names]))


def failing(exc: Exception):
    def read() -> pd.DataFrame:
        raise exc

    return read


def test_first_non_empty_source_wins_and_later_sources_are_not_called():
    second = Source("api", failing(AssertionError("must not be called")))
    result = FallbackReader(Source("db", lambda: frame("a")), second).load()
    assert (result.source, list(result.data["name"]), result.diagnostics) == ("db", ["a"], [])


def test_operational_failure_falls_back_without_leaking_driver_details():
    result = FallbackReader(
        Source("db", failing(StorageError("connection to host 10.0.0.5 user neo_user refused"))),
        Source("api", lambda: frame("b")),
    ).load()
    assert result.source == "api"
    assert len(result.diagnostics) == 1
    assert "10.0.0.5" not in result.diagnostics[0] and "neo_user" not in result.diagnostics[0]


def test_empty_source_reports_its_hint_and_falls_back():
    result = FallbackReader(
        Source("db", lambda: frame(), empty_hint="table is empty"), Source("api", lambda: frame("c"))
    ).load()
    assert result.source == "api"
    assert result.diagnostics == ["db: table is empty"]


def test_all_sources_failing_returns_an_empty_frame_with_every_reason():
    result = FallbackReader(
        Source("db", failing(ConfigError("Missing database settings: DB_PASSWORD"))),
        Source("api", failing(ConfigError("NASA_API_KEY is not set"))),
    ).load()
    assert result.source is None
    assert result.data.empty and list(result.data.columns) == COLUMNS
    assert [d.split(":")[0] for d in result.diagnostics] == ["db", "api"]


def test_programmer_errors_propagate_instead_of_triggering_the_fallback():
    with pytest.raises(KeyError):
        FallbackReader(Source("db", failing(KeyError("bug"))), Source("api", lambda: frame("x"))).load()


def test_normalize_converts_decimals_and_dates():
    df = normalize(pd.DataFrame([{"miss_distance_km": Decimal("1.5"), "close_approach_date": "2026-11-10"}]))
    assert df["miss_distance_km"].iloc[0] == 1.5
    assert df["close_approach_date"].iloc[0] == date(2026, 11, 10)
    assert df["is_potentially_hazardous"].dtype == bool and not df["is_potentially_hazardous"].iloc[0]


@responses.activate
def test_read_nasa_fetches_the_last_three_days_newest_first():
    responses.get(
        FEED,
        json=make_feed({"2026-11-08": [make_neo("old", "2026-11-08")], "2026-11-10": [make_neo("new", "2026-11-10")]}),
        match=[matchers.query_param_matcher({"start_date": "2026-11-08", "end_date": "2026-11-10"})],
    )
    df = read_nasa(Settings({"NASA_API_KEY": "k"}), today=date(2026, 11, 10))
    assert list(df["neo_reference_id"]) == ["new", "old"]
    assert list(df.columns) == COLUMNS
