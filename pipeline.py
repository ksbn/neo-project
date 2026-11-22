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
    7  NASA answered in an unexpected format (schema change; retrying won't help)
    130 interrupted (Ctrl+C)

An invalid or revoked NASA_API_KEY (HTTP 401/403) is a configuration error (1),
not an outage (3). This module is the composition root: it reads settings once,
builds the NASA client and the repository, hands them to
:class:`ingest.IngestService`, and maps typed errors to exit codes.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext
from datetime import date

from client import NeoWsClient
from config import Settings
from database import AsteroidRepository, connect
from errors import ExitCode, FeedContractError, NeoError, StorageError, UpstreamAuthError
from ingest import IngestService, PipelineStats, WindowFailed
from ports import AsteroidStore, FeedSource

__all__ = ["ExitCode", "PipelineStats", "WindowFailed", "main", "parse_args"]

logger = logging.getLogger("pipeline")

FeedFactory = Callable[[Settings], AbstractContextManager[FeedSource]]
StoreFactory = Callable[[Settings], AbstractContextManager[AsteroidStore]]


def open_nasa_feed(settings: Settings) -> NeoWsClient:
    """Default FeedSource: the real NeoWs client with the batch retry profile."""
    return NeoWsClient(settings.nasa_api_key())


@contextmanager
def open_postgres_store(settings: Settings) -> Iterator[AsteroidRepository]:
    """Default AsteroidStore: a repository on a fresh connection, closed afterwards."""
    with connect(settings.db()) as conn:
        yield AsteroidRepository(conn)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load NASA NeoWs asteroid data into PostgreSQL.",
        epilog="Exit codes: 0 ok, 1 config error (incl. rejected API key), 2 bad arguments, 3 NASA API unavailable, "
        "4 database error, 5 partial run (re-run the same range to finish), 7 NASA format changed, 130 interrupted.",
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


_FAILURE_LABELS: dict[type[NeoError], str] = {
    UpstreamAuthError: "NASA rejected the API key",
    FeedContractError: "NeoWs response did not match the expected format",
    StorageError: "Database error",
}


def _exit_code_for_failure(exc: WindowFailed, stats: PipelineStats, dry_run: bool) -> ExitCode:
    """Log a window failure and map it to an exit code."""
    cause = exc.cause
    label = next((text for kind, text in _FAILURE_LABELS.items() if isinstance(cause, kind)), "NASA API error")
    logger.error("%s on %s..%s: %s", label, exc.window_start, exc.window_end, cause)

    if stats.windows_done and not dry_run:
        logger.error(
            "Partial run: %d of %d windows were committed before the failure. "
            "Re-run the same range to finish (safe: the upsert is idempotent).",
            stats.windows_done, stats.windows_total,
        )
        return ExitCode.PARTIAL
    return cause.exit_code


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    open_feed: FeedFactory = open_nasa_feed,
    open_store: StoreFactory = open_postgres_store,
) -> int:
    """Run the CLI. The keyword arguments are the seams tests use to inject fakes."""
    args = parse_args(argv)
    configure_logging(args.log_level)

    stats = PipelineStats()
    try:
        settings = settings or Settings.load()
        # Open the store before any NASA call so a database outage doesn't waste API quota.
        with (
            nullcontext(None) if args.dry_run else open_store(settings)
        ) as store, open_feed(settings) as feed:
            IngestService(feed, store).run(args.start_date, args.end_date, stats)
    except WindowFailed as exc:
        return _exit_code_for_failure(exc, stats, args.dry_run)
    except NeoError as exc:
        logger.error("%s", exc)
        return exc.exit_code
    except KeyboardInterrupt:
        logger.warning(
            "Interrupted after %d of %d windows; completed windows were committed", stats.windows_done, stats.windows_total
        )
        return ExitCode.INTERRUPTED
    # Anything else is a bug: let it crash with a full traceback.

    logger.info(
        "Done: %d fetched, %d unique, %d inserted, %d updated, %d unchanged, %d invalid, %d missing days",
        stats.fetched, stats.unique, stats.inserted, stats.updated, stats.unchanged, stats.invalid,
        stats.missing_dates,
    )
    return ExitCode.OK


if __name__ == "__main__":
    sys.exit(main())
