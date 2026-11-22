"""PostgreSQL adapter (database.py): idempotent writes and the read side, against an isolated test schema.

See conftest.py: each test runs inside a rolled-back transaction in a
throwaway schema built from db/migrations, so these never touch real data.
"""

from datetime import date
from decimal import Decimal

import pytest

import models
from config import DbConfig
from conftest import make_feed, make_neo
from database import COLUMNS, AsteroidRepository, UpsertResult, _row, connect
from domain import Overview
from errors import StorageError
from models import parse_feed
from ui_messages import describe


def records(*neos, day="2026-11-10"):
    return parse_feed(make_feed({day: list(neos)})).observations


def test_insert_then_identical_rerun_is_noop(conn):
    repo = AsteroidRepository(conn)
    batch = records(make_neo("1"), make_neo("2"))

    assert repo.save(batch) == UpsertResult(inserted=2)
    assert repo.save(batch) == UpsertResult(unchanged=2)
    assert repo.count() == 2


def test_changed_values_update_in_place(conn):
    repo = AsteroidRepository(conn)
    repo.save(records(make_neo("1")))

    result = repo.save(records(make_neo("1", is_potentially_hazardous_asteroid=True)))

    assert result == UpsertResult(updated=1)
    (hazardous,) = conn.execute("SELECT is_potentially_hazardous FROM asteroids WHERE neo_reference_id = '1'").fetchone()
    assert hazardous is True
    assert repo.count() == 1


def test_older_approach_does_not_overwrite_newer(conn):
    repo = AsteroidRepository(conn)
    repo.save(records(make_neo("1", "2026-11-12"), day="2026-11-12"))

    result = repo.save(records(make_neo("1", "2026-11-01", name="old"), day="2026-11-01"))

    assert result == UpsertResult(unchanged=1)
    (name,) = conn.execute("SELECT name FROM asteroids").fetchone()
    assert name != "old"


def test_updated_at_moves_only_on_real_change(conn):
    repo = AsteroidRepository(conn)
    repo.save(records(make_neo("1")))
    conn.execute("UPDATE asteroids SET updated_at = '2000-01-01'")

    repo.save(records(make_neo("1")))
    (unchanged_ts,) = conn.execute("SELECT updated_at::date::text FROM asteroids").fetchone()
    repo.save(records(make_neo("1", name="renamed")))
    (changed_ts,) = conn.execute("SELECT updated_at::date::text FROM asteroids").fetchone()

    assert unchanged_ts == "2000-01-01"
    assert changed_ts != "2000-01-01"


def test_empty_batch_is_a_noop(conn):
    assert AsteroidRepository(conn).save([]) == UpsertResult()


def test_largest_accepted_values_fit_the_columns(conn):
    """Whatever the model accepts must be storable, so one record can't fail a batch."""
    neo = make_neo("1", absolute_magnitude_h="999.999")
    neo["close_approach_data"][0]["miss_distance"]["kilometers"] = "99999999999999999.999"
    neo["close_approach_data"][0]["relative_velocity"]["kilometers_per_second"] = "99999999.999999"
    assert AsteroidRepository(conn).save(records(neo)) == UpsertResult(inserted=1)


@pytest.mark.parametrize(
    ("column", "limit"),
    [
        ("absolute_magnitude_h", models.MAGNITUDE_LIMIT),
        ("estimated_diameter_min_km", models.DIAMETER_KM_LIMIT),
        ("estimated_diameter_max_km", models.DIAMETER_KM_LIMIT),
        ("relative_velocity_km_s", models.VELOCITY_KM_S_LIMIT),
        ("miss_distance_km", models.MISS_DISTANCE_KM_LIMIT),
    ],
)
def test_model_limits_match_column_types(conn, column, limit):
    """Fails if a migration changes a NUMERIC column without updating models.py."""
    precision, scale = conn.execute(
        "SELECT numeric_precision, numeric_scale FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = 'asteroids' AND column_name = %s",
        (column,),
    ).fetchone()
    assert limit == Decimal(10) ** (precision - scale) - Decimal("0.5") * Decimal(10) ** -scale


def test_list_recent_returns_newest_first_and_respects_limit(conn):
    repo = AsteroidRepository(conn)
    repo.save(records(make_neo("old", "2026-11-01"), day="2026-11-01"))
    repo.save(records(make_neo("new", "2026-11-12"), day="2026-11-12"))
    repo.save(records(make_neo("mid", "2026-11-05"), day="2026-11-05"))

    rows = repo.list_recent()
    assert [r.neo_reference_id for r in rows] == ["new", "mid", "old"]
    assert rows[0].close_approach_date == date(2026, 11, 12)
    assert rows[0].miss_distance_km == 4567890.123  # NUMERIC arrives as float, ready for display
    assert [r.neo_reference_id for r in repo.list_recent(limit=1)] == ["new"]


def test_overview_counts_the_whole_table_not_just_the_listed_rows(conn):
    repo = AsteroidRepository(conn)
    repo.save(records(make_neo("1", "2026-11-10"), make_neo("2", "2026-11-10", is_potentially_hazardous_asteroid=True)))
    repo.save(records(make_neo("3", "2026-11-12"), day="2026-11-12"))

    assert len(repo.list_recent(limit=1)) == 1
    assert repo.overview() == Overview(total=3, hazardous=1, latest_approach=date(2026, 11, 12))
    assert repo.count() == 3


def test_newest_observation_wins_within_one_batch(conn):
    feed = make_feed({"2026-11-10": [make_neo("1", name="old")], "2026-11-12": [make_neo("1", "2026-11-12", name="new")]})
    result = AsteroidRepository(conn).save(parse_feed(feed).observations)

    assert result == UpsertResult(inserted=1)
    assert conn.execute("SELECT name, close_approach_date FROM asteroids").fetchone() == ("new", date(2026, 11, 12))


def test_written_columns_match_the_table(conn):
    """COLUMNS and _row() must cover exactly the table's data columns."""
    table = {
        name
        for (name,) in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'asteroids'"
        ).fetchall()
    }
    assert set(COLUMNS) == table - {"created_at", "updated_at"}
    assert set(_row(records(make_neo("1"))[0])) == set(COLUMNS)


def test_driver_errors_become_storage_errors(conn):
    conn.execute("DROP TABLE asteroids")  # rolled back with the test transaction
    with pytest.raises(StorageError, match="SQLSTATE 42P01") as exc:
        AsteroidRepository(conn).count()
    assert "SQLSTATE" not in describe(exc.value)  # driver details stay out of the UI


def test_unreachable_server_is_a_storage_error():
    cfg = DbConfig(host="127.0.0.1", port=1, dbname="x", user="x", password="secret")
    with pytest.raises(StorageError, match="Could not connect to PostgreSQL at 127.0.0.1:1"):
        connect(cfg, connect_timeout=1)
