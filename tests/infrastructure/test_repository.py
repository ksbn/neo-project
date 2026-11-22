"""PostgreSQL repositories (infrastructure/repository.py): idempotent writes and reads, in a throwaway schema.

See conftest.py: each test runs inside a rolled-back transaction in a
throwaway schema built from db/migrations, so these never touch real data.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import LiteralString, cast

import pytest

from planetary_defense.config import DbConfig
from planetary_defense.domain.errors import StorageError
from planetary_defense.domain.models import ApproachSummary, IngestionRun, Overview, PipelineStatus, RunStatus
from planetary_defense.infrastructure import nasa_dto
from planetary_defense.infrastructure.db import connect
from planetary_defense.infrastructure.nasa_dto import parse_feed
from planetary_defense.infrastructure.repository import (
    COLUMNS,
    AsteroidRepository,
    IngestionRunRepository,
    UpsertResult,
    _row,
)
from planetary_defense.ui.messages import describe

from support import make_feed, make_neo, migration_up_sql


def records(*neos, day="2026-11-10"):
    return parse_feed(make_feed({day: list(neos)})).observations


def test_insert_then_identical_rerun_is_noop(conn):
    repo = AsteroidRepository(conn)
    batch = records(make_neo("1"), make_neo("2"))

    assert repo.save(batch) == UpsertResult(inserted=2, new_approaches=2)
    assert repo.save(batch) == UpsertResult(unchanged=2)
    assert repo.count() == 2


def test_changed_values_update_in_place(conn):
    repo = AsteroidRepository(conn)
    repo.save(records(make_neo("1")))

    result = repo.save(records(make_neo("1", is_potentially_hazardous_asteroid=True)))

    assert result == UpsertResult(updated=1)
    query = "SELECT is_potentially_hazardous FROM asteroids WHERE neo_reference_id = '1'"
    (hazardous,) = conn.execute(query).fetchone()
    assert hazardous is True
    assert repo.count() == 1


def test_older_approach_does_not_overwrite_newer(conn):
    repo = AsteroidRepository(conn)
    repo.save(records(make_neo("1", "2026-11-12"), day="2026-11-12"))

    result = repo.save(records(make_neo("1", "2026-11-01", name="old"), day="2026-11-01"))

    assert result == UpsertResult(unchanged=1, new_approaches=1)  # the older pass is kept as history
    (name,) = conn.execute("SELECT name FROM asteroids").fetchone()
    assert name != "old"
    assert [a.close_approach_date for a in repo.approach_history("1")] == [date(2026, 11, 12), date(2026, 11, 1)]


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
    assert AsteroidRepository(conn).save(records(neo)) == UpsertResult(inserted=1, new_approaches=1)


@pytest.mark.parametrize(
    ("table", "column", "limit"),
    [
        ("asteroids", "absolute_magnitude_h", nasa_dto.MAGNITUDE_LIMIT),
        ("asteroids", "estimated_diameter_min_km", nasa_dto.DIAMETER_KM_LIMIT),
        ("asteroids", "estimated_diameter_max_km", nasa_dto.DIAMETER_KM_LIMIT),
        ("asteroids", "relative_velocity_km_s", nasa_dto.VELOCITY_KM_S_LIMIT),
        ("asteroids", "miss_distance_km", nasa_dto.MISS_DISTANCE_KM_LIMIT),
        ("close_approaches", "relative_velocity_km_s", nasa_dto.VELOCITY_KM_S_LIMIT),
        ("close_approaches", "miss_distance_km", nasa_dto.MISS_DISTANCE_KM_LIMIT),
    ],
)
def test_model_limits_match_column_types(conn, table, column, limit):
    """Fails if a migration changes a NUMERIC column without updating models.py."""
    row = conn.execute(
        "SELECT numeric_precision, numeric_scale FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = %s AND column_name = %s",
        (table, column),
    ).fetchone()
    assert row is not None, f"{table}.{column} is missing"
    precision, scale = row
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
    assert repo.overview() == Overview(total=3, hazardous=1, latest_approach=date(2026, 11, 12), approaches=3)
    assert repo.count() == 3


def test_newest_observation_wins_within_one_batch(conn):
    feed = make_feed(
        {"2026-11-10": [make_neo("1", name="old")], "2026-11-12": [make_neo("1", "2026-11-12", name="new")]}
    )
    result = AsteroidRepository(conn).save(parse_feed(feed).observations)

    assert result == UpsertResult(inserted=1, new_approaches=2)
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
    conn.execute("DROP TABLE asteroids CASCADE")  # rolled back with the test transaction
    with pytest.raises(StorageError, match="SQLSTATE 42P01") as exc:
        AsteroidRepository(conn).count()
    assert "SQLSTATE" not in describe(exc.value)  # driver details stay out of the UI


def test_unreachable_server_is_a_storage_error():
    cfg = DbConfig(host="127.0.0.1", port=1, dbname="x", user="x", password="secret")
    with pytest.raises(StorageError, match="Could not connect to PostgreSQL at 127.0.0.1:1"):
        connect(cfg, connect_timeout=1)


# --------------------------------------------------------------------------- #
# History (close_approaches)
# --------------------------------------------------------------------------- #


def test_every_pass_is_kept_as_history_newest_first(conn):
    repo = AsteroidRepository(conn)
    for day in ("2026-11-01", "2026-11-12", "2026-11-05"):
        repo.save(records(make_neo("1", day), day=day))

    history = repo.approach_history("1")
    assert [a.close_approach_date for a in history] == [date(2026, 11, 12), date(2026, 11, 5), date(2026, 11, 1)]
    assert history[0] == ApproachSummary(date(2026, 11, 12), "Earth", 12.345678, 4567890.123)
    assert [a.close_approach_date for a in repo.approach_history("1", limit=1)] == [date(2026, 11, 12)]
    assert repo.approach_history("unknown") == []


def test_revised_numbers_update_the_pass_instead_of_adding_one(conn):
    repo = AsteroidRepository(conn)
    repo.save(records(make_neo("1")))
    revised = make_neo("1")
    revised["close_approach_data"][0]["miss_distance"]["kilometers"] = "1.5"

    assert repo.save(records(revised)).new_approaches == 0
    (history,) = repo.approach_history("1")
    assert history.miss_distance_km == 1.5


def test_migration_seeds_history_from_existing_asteroids(conn):
    """The close_approaches migration copies each stored asteroid's approach."""
    AsteroidRepository(conn).save(records(make_neo("1")))
    conn.execute("DELETE FROM close_approaches")

    seed = next(up for up in migration_up_sql() if "INSERT INTO close_approaches" in up)
    conn.execute(cast(LiteralString, seed))  # re-runnable: CREATE ... IF NOT EXISTS, ON CONFLICT DO NOTHING
    assert [a.close_approach_date for a in AsteroidRepository(conn).approach_history("1")] == [date(2026, 11, 10)]


# --------------------------------------------------------------------------- #
# Runs (ingestion_runs)
# --------------------------------------------------------------------------- #

T0 = datetime(2026, 11, 22, 8, 0, tzinfo=UTC)


def a_run(status: RunStatus, finished: datetime, **kw) -> IngestionRun:
    error = {} if status is RunStatus.SUCCESS else {"error_category": "UpstreamUnavailable", "error_message": "down"}
    return IngestionRun(
        started_at=finished - timedelta(minutes=1), finished_at=finished,
        start_date=date(2026, 11, 20), end_date=date(2026, 11, 22), status=status, **{**error, **kw},
    )


def test_pipeline_status_is_empty_before_any_run(conn):
    assert IngestionRunRepository(conn).pipeline_status() == PipelineStatus(last_run=None, last_success_at=None)


def test_records_runs_and_reports_latest_and_last_success(conn):
    runs = IngestionRunRepository(conn)
    runs.record(a_run(RunStatus.SUCCESS, T0, inserted=3, new_approaches=4))
    runs.record(a_run(RunStatus.FAILED, T0 + timedelta(days=1)))

    status = runs.pipeline_status()
    assert status.last_success_at == T0
    assert status.last_run == a_run(RunStatus.FAILED, T0 + timedelta(days=1))


def test_long_error_messages_are_truncated(conn):
    runs = IngestionRunRepository(conn)
    runs.record(a_run(RunStatus.FAILED, T0, error_message="x" * 5_000))
    last = runs.pipeline_status().last_run
    assert last is not None and last.error_message is not None and len(last.error_message) == 1_000


def test_status_and_error_must_agree(conn):
    """The table rejects a 'success' with an error, so a recorder bug can't hide a failure."""
    with pytest.raises(StorageError, match="chk_ingestion_runs_error"):
        IngestionRunRepository(conn).record(a_run(RunStatus.SUCCESS, T0, error_category="StorageError"))
