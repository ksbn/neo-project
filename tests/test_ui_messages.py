"""The dashboard's pipeline-status wording (ui_messages.run_health). No Streamlit."""

from datetime import UTC, date, datetime, timedelta

from domain import IngestionRun, PipelineStatus, RunStatus
from ui_messages import run_health

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)


def a_run(status: RunStatus, hours_ago: float, category: str | None = None) -> IngestionRun:
    finished = NOW - timedelta(hours=hours_ago)
    return IngestionRun(
        started_at=finished - timedelta(minutes=1), finished_at=finished,
        start_date=date(2026, 11, 20), end_date=date(2026, 11, 22), status=status,
        inserted=3, updated=2, new_approaches=5, error_category=category,
    )


def test_unknown_and_empty_status_are_informational():
    assert run_health(None, NOW).level == "info"
    health = run_health(PipelineStatus(last_run=None, last_success_at=None), NOW)
    assert health.level == "info" and "No pipeline runs" in health.text


def test_recent_success_summarises_the_run():
    run = a_run(RunStatus.SUCCESS, hours_ago=4)
    health = run_health(PipelineStatus(run, run.finished_at), NOW)
    assert health.level == "success"
    assert "4 hours ago" in health.text and "3 new, 2 updated asteroids, 5 new approaches" in health.text


def test_old_success_is_flagged_as_stale():
    run = a_run(RunStatus.SUCCESS, hours_ago=72)
    health = run_health(PipelineStatus(run, run.finished_at), NOW)
    assert health.level == "warning" and "stale" in health.text and "3 days ago" in health.text


def test_failed_and_partial_runs_name_the_cause_and_the_last_success():
    failed = run_health(PipelineStatus(a_run(RunStatus.FAILED, 1, "UpstreamAuthError"), NOW - timedelta(hours=25)), NOW)
    assert failed.level == "error"
    assert "NASA rejecting the API key" in failed.text and "nothing was saved" in failed.text
    assert "Last successful run: 25 hours ago" in failed.text

    partial = run_health(PipelineStatus(a_run(RunStatus.PARTIAL, 1, "StorageError"), None), NOW)
    assert partial.level == "error" and "a database error" in partial.text and "some days were saved" in partial.text


def test_unknown_error_category_still_reads_well():
    health = run_health(PipelineStatus(a_run(RunStatus.FAILED, 1, "SomethingNew"), None), NOW)
    assert "an unexpected error" in health.text
