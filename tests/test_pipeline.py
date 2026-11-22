"""End-to-end orchestration (pipeline.py): argument parsing and exit codes.

NASA is simulated with ``responses``; database writes go to the rolled-back
test connection from conftest.py. Collaborators are injected through
``main(settings=..., open_feed=..., open_store=...)``; nothing is monkeypatched.
"""

import logging
from contextlib import nullcontext
from datetime import date

import pytest
import responses

from client import BASE_URL, NeoWsClient
from config import Settings
from conftest import make_feed, make_neo
from database import AsteroidRepository
from errors import NeoError, StorageError
from ingest import DryRunStore
from pipeline import CLI_OUTCOMES, ExitCode, main, outcome, parse_args

FEED = f"{BASE_URL}/feed"
ENV = {"NASA_API_KEY": "test-key", "DB_HOST": "127.0.0.1", "DB_NAME": "x", "DB_USER": "x", "DB_PASSWORD": "x"}


def fast_feed(settings: Settings) -> NeoWsClient:
    """The real client with no retry backoff."""
    return NeoWsClient(settings.nasa_api_key(), backoff_factor=0)


def no_store(settings: Settings):
    raise AssertionError("this test must not open the database")


def run(argv, *, env=ENV, open_feed=fast_feed, open_store=no_store) -> int:
    return main(argv, settings=Settings(env), open_feed=open_feed, open_store=open_store)


@pytest.fixture
def test_db(conn):
    """A store factory bound to the rolled-back test connection."""
    return lambda settings: nullcontext(AsteroidRepository(conn))


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

    assert run(["--start", "2026-11-01", "--end", "2026-11-07"], open_store=test_db) == ExitCode.OK
    assert count(test_db) == 7


@responses.activate
def test_rerun_is_idempotent(test_db):
    responses.get(FEED, json=week_feed(date(2026, 11, 1), "a"))
    argv = ["--start", "2026-11-01", "--end", "2026-11-07"]

    assert run(argv, open_store=test_db) == run(argv, open_store=test_db) == ExitCode.OK
    assert count(test_db) == 7


@responses.activate
def test_nasa_down_exits_3(test_db):
    responses.get(FEED, status=503)
    assert run(["--start", "2026-11-01"], open_store=test_db) == ExitCode.API_UNAVAILABLE
    assert count(test_db) == 0


@responses.activate
def test_rejected_api_key_is_a_config_error_not_an_outage(caplog):
    responses.get(FEED, status=403, json={"error": {"code": "API_KEY_INVALID", "message": "An invalid api_key was supplied."}})
    assert run(["--start", "2026-11-01", "--dry-run"]) == ExitCode.CONFIG_ERROR
    assert "NASA rejected the API key" in caplog.text


@responses.activate
def test_malformed_envelope_exits_7():
    responses.get(FEED, json={"unexpected": True})
    assert run(["--start", "2026-11-01", "--dry-run"]) == ExitCode.CONTRACT_ERROR


@responses.activate
def test_failure_after_committed_window_exits_5_and_keeps_progress(test_db, caplog):
    responses.get(FEED, json=week_feed(date(2026, 11, 1), "a"))  # window 1 succeeds
    responses.get(FEED, status=503)  # window 2 fails on every retry

    code = run(["--start", "2026-11-01", "--end", "2026-11-10"], open_store=test_db)

    assert code == ExitCode.PARTIAL
    assert count(test_db) == 7
    assert "1 of 2 windows were committed" in caplog.text
    assert "2026-11-08..2026-11-10" in caplog.text


def test_database_unreachable_exits_4():
    # The real store factory against a port nothing listens on.
    assert main(["--start", "2026-11-01"], settings=Settings({**ENV, "DB_PORT": "1"})) == ExitCode.DATABASE_ERROR


def test_missing_db_settings_exit_1(caplog):
    env = {k: v for k, v in ENV.items() if k != "DB_PASSWORD"}
    assert main(["--start", "2026-11-01"], settings=Settings(env)) == ExitCode.CONFIG_ERROR
    assert "DB_PASSWORD" in caplog.text


def test_missing_nasa_key_exits_1_even_for_a_dry_run():
    assert run(["--start", "2026-11-01", "--dry-run"], env={}) == ExitCode.CONFIG_ERROR


def test_programmer_errors_are_not_swallowed():
    class BrokenFeed:
        max_window_days = 7

        def fetch_records(self, start_date, end_date):
            raise KeyError("bug")

    with pytest.raises(KeyError):
        run(["--start", "2026-11-01", "--dry-run"], open_feed=lambda settings: nullcontext(BrokenFeed()))


@responses.activate
def test_missing_day_in_response_is_warned(caplog):
    responses.get(FEED, json=make_feed({"2026-11-01": [make_neo("1", "2026-11-01")]}))

    with caplog.at_level(logging.WARNING):
        code = run(["--start", "2026-11-01", "--end", "2026-11-02", "--dry-run"])

    assert code == ExitCode.OK
    assert "no entry for 2026-11-02" in caplog.text


def count(store_factory) -> int:
    with store_factory(None) as store:
        return store.count()


@responses.activate
def test_rejected_request_is_not_reported_as_an_outage():
    responses.get(FEED, status=400, json={"code": 400, "error_message": "Date Format Exception"})
    assert run(["--start", "2026-11-01", "--dry-run"]) == ExitCode.CONTRACT_ERROR


@responses.activate
def test_failing_final_count_does_not_turn_success_into_an_error(caplog):
    class CountFails(DryRunStore):
        def count(self) -> int:
            raise StorageError("connection lost")

    responses.get(FEED, json=make_feed({"2026-11-01": [make_neo("1", "2026-11-01")]}))
    assert run(["--start", "2026-11-01"], open_store=lambda settings: nullcontext(CountFails())) == ExitCode.OK
    assert "Could not count stored asteroids" in caplog.text


def test_every_error_class_maps_to_an_outcome():
    """The handler can never crash on a missing exit code, whatever subclass is raised."""

    class BrandNew(NeoError):
        pass

    def subclasses(kind):
        for sub in kind.__subclasses__():
            yield sub
            yield from subclasses(sub)

    for kind in [BrandNew, *(k for k in subclasses(NeoError) if k.__module__ == "errors"), NeoError]:
        code, label = outcome(kind("x"))
        assert isinstance(code, ExitCode) and label
    assert outcome(BrandNew("x")) == CLI_OUTCOMES[NeoError]
