"""End-to-end orchestration (pipeline.py): argument parsing and exit codes.

NASA is simulated with ``responses``; database writes go to the rolled-back
test connection from conftest.py.
"""

import functools
import logging
from contextlib import nullcontext
from datetime import date

import psycopg
import pytest
import responses

import pipeline
from client import BASE_URL, NeoWsClient
from conftest import make_feed, make_neo
from database import AsteroidRepository
from pipeline import ExitCode, main, parse_args

FEED = f"{BASE_URL}/feed"


@pytest.fixture(autouse=True)
def fast_client(monkeypatch):
    """Use a fixed fake key and no retry backoff inside the pipeline."""
    monkeypatch.setattr(pipeline, "NeoWsClient", functools.partial(NeoWsClient, api_key="test-key", backoff_factor=0))


@pytest.fixture
def test_db(monkeypatch, conn):
    """Point the pipeline at the rolled-back test connection instead of the real database."""
    monkeypatch.setattr(pipeline, "connect", lambda: nullcontext(conn))
    return conn


def week_feed(start: date, ref_prefix: str) -> dict:
    days = [date.fromordinal(start.toordinal() + i).isoformat() for i in range(7)]
    return make_feed({d: [make_neo(f"{ref_prefix}{i}", d)] for i, d in enumerate(days)})


# --------------------------------------------------------------------------- #
# Arguments
# --------------------------------------------------------------------------- #


def test_short_and_long_date_options_are_equivalent():
    short = parse_args(["--start", "2026-11-01", "--end", "2026-11-03"])
    long = parse_args(["--start-date", "2026-11-01", "--end-date", "2026-11-03"])
    assert (short.start_date, short.end_date) == (long.start_date, long.end_date) == (date(2026, 11, 1), date(2026, 11, 3))


def test_defaults_to_today():
    args = parse_args([])
    assert args.start_date == args.end_date == date.today()


def test_single_start_date_means_one_day():
    args = parse_args(["--start-date", "2026-11-05"])
    assert args.start_date == args.end_date == date(2026, 11, 5)


@pytest.mark.parametrize("argv", [["--start", "2026-11-10", "--end", "2026-11-01"], ["--start", "11/10/2026"]])
def test_bad_arguments_exit_2(argv):
    with pytest.raises(SystemExit) as exc:
        parse_args(argv)
    assert exc.value.code == ExitCode.USAGE_ERROR


# --------------------------------------------------------------------------- #
# Exit codes
# --------------------------------------------------------------------------- #


@responses.activate
def test_success_writes_records(test_db):
    responses.get(FEED, json=week_feed(date(2026, 11, 1), "a"))

    assert main(["--start", "2026-11-01", "--end", "2026-11-07"]) == ExitCode.OK
    assert AsteroidRepository(test_db).count() == 7


@responses.activate
def test_rerun_is_idempotent(test_db):
    responses.get(FEED, json=week_feed(date(2026, 11, 1), "a"))
    argv = ["--start", "2026-11-01", "--end", "2026-11-07"]

    assert main(argv) == main(argv) == ExitCode.OK
    assert AsteroidRepository(test_db).count() == 7


@responses.activate
def test_nasa_down_exits_3(test_db):
    responses.get(FEED, status=503)
    assert main(["--start", "2026-11-01"]) == ExitCode.API_UNAVAILABLE
    assert AsteroidRepository(test_db).count() == 0


@responses.activate
def test_malformed_envelope_exits_3():
    responses.get(FEED, json={"unexpected": True})
    assert main(["--start", "2026-11-01", "--dry-run"]) == ExitCode.API_UNAVAILABLE


@responses.activate
def test_failure_after_committed_window_exits_5_and_keeps_progress(test_db, caplog):
    responses.get(FEED, json=week_feed(date(2026, 11, 1), "a"))  # window 1 succeeds
    responses.get(FEED, status=503)  # window 2 fails on every retry

    code = main(["--start", "2026-11-01", "--end", "2026-11-10"])

    assert code == ExitCode.PARTIAL
    assert AsteroidRepository(test_db).count() == 7
    assert "1 of 2 windows were committed" in caplog.text
    assert "2026-11-08..2026-11-10" in caplog.text


def test_database_unreachable_exits_4(monkeypatch):
    def refuse():
        raise psycopg.OperationalError("connection refused")

    monkeypatch.setattr(pipeline, "connect", refuse)
    assert main(["--start", "2026-11-01"]) == ExitCode.DATABASE_ERROR


def test_missing_db_settings_exit_1(monkeypatch):
    def missing():
        raise ValueError("Missing database settings in environment/.env: DB_PASSWORD")

    monkeypatch.setattr(pipeline, "connect", missing)
    assert main(["--start", "2026-11-01"]) == ExitCode.CONFIG_ERROR


@responses.activate
def test_missing_day_in_response_is_warned(caplog):
    responses.get(FEED, json=make_feed({"2026-11-01": [make_neo("1", "2026-11-01")]}))

    with caplog.at_level(logging.WARNING):
        code = main(["--start", "2026-11-01", "--end", "2026-11-02", "--dry-run"])

    assert code == ExitCode.OK
    assert "no entry for 2026-11-02" in caplog.text
