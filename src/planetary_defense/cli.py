"""Fetch NASA NeoWs asteroid data for a date range and upsert it into PostgreSQL.

Usage::

    python pipeline.py                                  # today only
    python pipeline.py --start-date 2024-01-01          # a single day
    python pipeline.py --start 2024-01-01 --end 2024-01-20
    python pipeline.py --start-date 2024-01-01 --dry-run   # fetch + validate, no DB writes

Long ranges are fetched in 7-day windows (the NeoWs limit). Each window is
written in its own transaction, so an interrupted run keeps completed windows,
and re-running any range is safe because the upsert is idempotent.

Exit codes (see :class:`ExitCode`):
    0  success
    1  configuration error (e.g. missing .env settings)
    2  invalid command-line arguments
    3  NASA API unavailable or rate limited (retry later)
    4  database error
    5  partial run: some windows were committed before a later window failed
    7  NASA rejected the request (other HTTP 4xx) or answered in an unexpected format;
       retrying won't help
    130 interrupted (Ctrl+C)

An invalid or revoked NASA_API_KEY (HTTP 401/403) is a configuration error (1),
not an outage (3). This module is the composition root: it reads settings once,
builds the NASA client and the repository, hands them to
:class:`application.ingest_feed.IngestService`, and maps typed errors to exit codes and log
labels through :data:`CLI_OUTCOMES`. That table is the CLI's only knowledge of
the error hierarchy.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext
from datetime import date
from enum import IntEnum

from planetary_defense.application.ingest_feed import (
    AlertRecorder,
    DryRunStore,
    IngestionRunRecorder,
    IngestService,
    LogObserver,
    PipelineStats,
    RunObserver,
    WindowFailed,
)
from planetary_defense.application.ports import AlertStore, AsteroidStore, FeedSource, RunLog
from planetary_defense.config import Settings
from planetary_defense.domain.anomaly_rules import default_rules
from planetary_defense.domain.errors import (
    ConfigError,
    FeedContractError,
    NeoError,
    StorageError,
    UpstreamAuthError,
    UpstreamError,
    UpstreamRequestError,
)
from planetary_defense.infrastructure.db import connect
from planetary_defense.infrastructure.nasa_client import NeoWsClient
from planetary_defense.infrastructure.repository import AlertRepository, AsteroidRepository, IngestionRunRepository

__all__ = ["CLI_OUTCOMES", "ExitCode", "PipelineStats", "WindowFailed", "main", "outcome", "parse_args"]

logger = logging.getLogger("pipeline")


class ExitCode(IntEnum):
    """Process exit codes (6 is reserved for ``scripts/migrate.sh``)."""

    OK = 0
    CONFIG_ERROR = 1
    USAGE_ERROR = 2
    API_UNAVAILABLE = 3
    DATABASE_ERROR = 4
    PARTIAL = 5
    CONTRACT_ERROR = 7
    INTERRUPTED = 130


# Most specific class wins (see outcome()); every NeoError resolves through NeoError itself.
CLI_OUTCOMES: dict[type[NeoError], tuple[ExitCode, str]] = {
    ConfigError: (ExitCode.CONFIG_ERROR, "Configuration error"),
    UpstreamAuthError: (ExitCode.CONFIG_ERROR, "NASA rejected the API key"),
    UpstreamRequestError: (ExitCode.CONTRACT_ERROR, "NASA rejected the request"),
    FeedContractError: (ExitCode.CONTRACT_ERROR, "NeoWs response did not match the expected format"),
    UpstreamError: (ExitCode.API_UNAVAILABLE, "NASA API error"),
    StorageError: (ExitCode.DATABASE_ERROR, "Database error"),
    NeoError: (ExitCode.CONFIG_ERROR, "Unexpected operational error"),
}


def outcome(exc: NeoError) -> tuple[ExitCode, str]:
    """Exit code and log label for ``exc``. Never raises, whatever the subclass."""
    for kind in type(exc).__mro__:
        if kind in CLI_OUTCOMES:
            return CLI_OUTCOMES[kind]
    raise AssertionError("unreachable: NeoError is always in CLI_OUTCOMES")  # pragma: no cover

FeedFactory = Callable[[Settings], AbstractContextManager[FeedSource]]
StoreFactory = Callable[[Settings], AbstractContextManager[AsteroidStore]]
RunLogFactory = Callable[[Settings], AbstractContextManager[RunLog]]
AlertStoreFactory = Callable[[Settings], AbstractContextManager[AlertStore]]


def open_nasa_feed(settings: Settings) -> NeoWsClient:
    """Default FeedSource: the real NeoWs client with the batch retry profile."""
    return NeoWsClient(settings.nasa_api_key())


@contextmanager
def open_postgres_store(settings: Settings) -> Iterator[AsteroidRepository]:
    """Default AsteroidStore: a repository on a fresh connection, closed afterwards."""
    with connect(settings.db()) as conn:
        yield AsteroidRepository(conn)


@contextmanager
def open_postgres_run_log(settings: Settings) -> Iterator[IngestionRunRepository]:
    """Default RunLog: its own autocommit connection, so a run is recorded even
    when a write on the data connection failed and was rolled back."""
    with connect(settings.db(), autocommit=True, application_name="neo-pipeline-runlog") as conn:
        yield IngestionRunRepository(conn)


@contextmanager
def open_postgres_alert_store(settings: Settings) -> Iterator[AlertRepository]:
    """Default AlertStore: its own connection, so alerts never share a transaction with the data."""
    with connect(settings.db(), application_name="neo-pipeline-alerts") as conn:
        yield AlertRepository(conn)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load NASA NeoWs asteroid data into PostgreSQL.",
        epilog="Exit codes: 0 ok, 1 config error (incl. rejected API key), 2 bad arguments, 3 NASA API unavailable, "
        "4 database error, 5 partial run (re-run the same range to finish), 7 NASA rejected the request or changed "
        "its format, 130 interrupted.",
    )
    parser.add_argument(
        "--start-date", "--start",
        type=date.fromisoformat,
        help="First day to fetch (YYYY-MM-DD). Defaults to --end-date, or today.",
    )
    parser.add_argument(
        "--end-date", "--end",
        type=date.fromisoformat,
        help="Last day to fetch, inclusive (YYYY-MM-DD). Defaults to --start-date.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Fetch and validate without writing to the database.")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO).",
    )
    args = parser.parse_args(argv)

    args.start_date = args.start_date or args.end_date or date.today()
    args.end_date = args.end_date or args.start_date
    if args.end_date < args.start_date:
        parser.error(f"--end-date ({args.end_date}) is before --start-date ({args.start_date})")  # exits with 2
    return args


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _exit_code_for_failure(exc: WindowFailed, stats: PipelineStats, dry_run: bool) -> ExitCode:
    """Log a window failure and map it to an exit code."""
    code, label = outcome(exc.cause)
    logger.error("%s on %s..%s: %s", label, exc.window_start, exc.window_end, exc.cause)

    if stats.windows_done and not dry_run:
        logger.error(
            "Partial run: %d of %d windows were committed before the failure (cause: %s, exit code %d without "
            "the partial progress). Re-run the same range to finish (safe: the upsert is idempotent).",
            stats.windows_done, stats.windows_total, label, code,
        )
        return ExitCode.PARTIAL
    return code


def _log_table_size(store: AsteroidStore) -> None:
    """Informational only: every window is already committed, so a failure here must not change the exit code."""
    try:
        logger.info("Table now holds %d asteroids", store.count())
    except StorageError as exc:
        logger.warning("Could not count stored asteroids: %s", exc)


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    open_feed: FeedFactory = open_nasa_feed,
    open_store: StoreFactory = open_postgres_store,
    open_run_log: RunLogFactory = open_postgres_run_log,
    open_alert_store: AlertStoreFactory = open_postgres_alert_store,
) -> int:
    """Run the CLI. The keyword arguments are the seams tests use to inject fakes."""
    args = parse_args(argv)
    configure_logging(args.log_level)

    stats = PipelineStats()
    try:
        settings = settings or Settings.load()
        rules = default_rules(settings.alert_thresholds())  # bad thresholds stop the run before any fetch
        # Open the store before any NASA call so a database outage doesn't waste API quota.
        store_cm = nullcontext(DryRunStore()) if args.dry_run else open_store(settings)
        # A dry run writes nothing, not even a run record.
        run_log_cm = nullcontext(None) if args.dry_run else open_run_log(settings)
        alerts_cm = nullcontext(None) if args.dry_run else open_alert_store(settings)
        with store_cm as store, run_log_cm as run_log, alerts_cm as alert_store, open_feed(settings) as feed:
            observers: list[RunObserver] = [LogObserver(logger, dry_run=args.dry_run)]
            if alert_store is not None:
                observers.append(AlertRecorder(rules, alert_store))
            if run_log is not None:
                observers.append(IngestionRunRecorder(run_log))
            service = IngestService(feed, store, observers)
            service.run(args.start_date, args.end_date, stats)
            if not args.dry_run:
                _log_table_size(store)
    except WindowFailed as exc:
        return _exit_code_for_failure(exc, stats, args.dry_run)
    except NeoError as exc:
        code, label = outcome(exc)
        logger.error("%s: %s", label, exc)
        return code
    except KeyboardInterrupt:
        logger.warning(
            "Interrupted after %d of %d windows; completed windows were committed",
            stats.windows_done, stats.windows_total,
        )
        return ExitCode.INTERRUPTED
    # Anything else is a bug: let it crash with a full traceback.
    return ExitCode.OK


if __name__ == "__main__":
    sys.exit(main())
