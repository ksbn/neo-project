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
    3  NASA API unavailable or returned unusable data (retry later)
    4  database error
    5  partial run: some windows were committed before a later window failed
    130 interrupted (Ctrl+C)
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date, timedelta
from enum import IntEnum

import psycopg
from dotenv import load_dotenv
from pydantic import ValidationError

from client import NasaApiError, NeoWsClient, date_chunks
from database import AsteroidRepository, connect
from models import parse_feed

logger = logging.getLogger("pipeline")


class ExitCode(IntEnum):
    OK = 0
    CONFIG_ERROR = 1
    USAGE_ERROR = 2
    API_UNAVAILABLE = 3
    DATABASE_ERROR = 4
    PARTIAL = 5
    INTERRUPTED = 130


@dataclass
class PipelineStats:
    """Running totals across all fetched windows."""

    windows_total: int = 0
    windows_done: int = 0
    fetched: int = 0
    invalid: int = 0
    unique: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    missing_dates: int = 0


class WindowFailed(Exception):
    """A single 7-day window failed; earlier windows may already be committed."""

    def __init__(self, window_start: date, window_end: date, cause: Exception) -> None:
        super().__init__(f"window {window_start}..{window_end} failed: {cause}")
        self.window_start = window_start
        self.window_end = window_end
        self.cause = cause


def _process_window(
    client: NeoWsClient,
    repo: AsteroidRepository | None,
    window_start: date,
    window_end: date,
    stats: PipelineStats,
) -> None:
    """Fetch, validate and (unless ``repo`` is None) upsert one window."""
    result = parse_feed(client.get_feed(window_start, window_end))
    stats.fetched += result.element_count
    stats.invalid += len(result.invalid)
    stats.unique += len(result.records)

    for bad in result.invalid:
        logger.warning(
            "Skipped invalid asteroid %s on %s: %s",
            bad.neo_reference_id or "<unknown id>",
            bad.feed_date,
            bad.error,
        )

    requested = {window_start + timedelta(days=i) for i in range((window_end - window_start).days + 1)}
    missing = sorted(requested - result.feed_dates)
    if missing:
        stats.missing_dates += len(missing)
        logger.warning(
            "NeoWs returned no entry for %s; data for those days may be incomplete",
            ", ".join(d.isoformat() for d in missing),
        )

    if repo is None:
        logger.info(
            "%s..%s: %d objects, %d valid unique asteroids (not written: dry run)",
            window_start, window_end, result.element_count, len(result.records),
        )
        return

    upsert = repo.upsert_many(result.records)
    stats.inserted += upsert.inserted
    stats.updated += upsert.updated
    stats.unchanged += upsert.unchanged
    logger.info(
        "%s..%s: %d objects -> %d inserted, %d updated, %d unchanged, %d invalid",
        window_start, window_end, result.element_count,
        upsert.inserted, upsert.updated, upsert.unchanged, len(result.invalid),
    )


def run(
    start_date: date,
    end_date: date,
    *,
    dry_run: bool = False,
    stats: PipelineStats | None = None,
) -> PipelineStats:
    """Fetch, validate and store asteroids for the inclusive range ``start_date``..``end_date``.

    Pass ``stats`` to observe progress even if the run fails part-way.

    Raises:
        ValueError: Invalid date range or missing configuration.
        WindowFailed: A window failed (NASA API, response format or database error).
        psycopg.Error: The database connection could not be opened.
    """
    if end_date < start_date:
        raise ValueError(f"--end-date ({end_date}) is before --start-date ({start_date})")

    stats = stats if stats is not None else PipelineStats()
    windows = list(date_chunks(start_date, end_date))
    stats.windows_total = len(windows)
    logger.info(
        "Starting pipeline for %s to %s (%d window%s)%s",
        start_date, end_date, len(windows), "" if len(windows) == 1 else "s", " (dry run)" if dry_run else "",
    )

    # Connect before calling NASA so a database outage doesn't waste API quota.
    with NeoWsClient() as client, (nullcontext(None) if dry_run else connect()) as conn:
        repo = AsteroidRepository(conn) if conn is not None else None

        for window_start, window_end in windows:
            try:
                _process_window(client, repo, window_start, window_end, stats)
            except (NasaApiError, ValidationError, psycopg.Error) as exc:
                raise WindowFailed(window_start, window_end, exc) from exc
            stats.windows_done += 1

        if repo is not None:
            logger.info("Table now holds %d asteroids", repo.count())

    return stats


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load NASA NeoWs asteroid data into PostgreSQL.",
        epilog="Exit codes: 0 ok, 1 config error, 2 bad arguments, 3 NASA API unavailable, "
        "4 database error, 5 partial run (re-run the same range to finish), 130 interrupted.",
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
    cause = exc.cause
    if isinstance(cause, NasaApiError):
        logger.error("NASA API error on %s..%s: %s", exc.window_start, exc.window_end, cause)
        code = ExitCode.API_UNAVAILABLE
    elif isinstance(cause, ValidationError):
        logger.error(
            "NeoWs response for %s..%s did not match the expected format: %s", exc.window_start, exc.window_end, cause
        )
        code = ExitCode.API_UNAVAILABLE
    else:
        logger.error("Database error on %s..%s: %s", exc.window_start, exc.window_end, cause)
        code = ExitCode.DATABASE_ERROR

    if stats.windows_done and not dry_run:
        logger.error(
            "Partial run: %d of %d windows were committed before the failure. "
            "Re-run the same range to finish (safe: the upsert is idempotent).",
            stats.windows_done, stats.windows_total,
        )
        return ExitCode.PARTIAL
    return code


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.log_level)
    load_dotenv()

    stats = PipelineStats()
    try:
        run(args.start_date, args.end_date, dry_run=args.dry_run, stats=stats)
    except WindowFailed as exc:
        return _exit_code_for_failure(exc, stats, args.dry_run)
    except ValueError as exc:
        logger.error("Configuration error: %s", exc)
        return ExitCode.CONFIG_ERROR
    except psycopg.Error as exc:
        logger.error("Database error: %s", exc)
        return ExitCode.DATABASE_ERROR
    except KeyboardInterrupt:
        logger.warning(
            "Interrupted after %d of %d windows; completed windows were committed", stats.windows_done, stats.windows_total
        )
        return ExitCode.INTERRUPTED

    logger.info(
        "Done: %d fetched, %d unique, %d inserted, %d updated, %d unchanged, %d invalid, %d missing days",
        stats.fetched, stats.unique, stats.inserted, stats.updated, stats.unchanged, stats.invalid,
        stats.missing_dates,
    )
    return ExitCode.OK


if __name__ == "__main__":
    sys.exit(main())
