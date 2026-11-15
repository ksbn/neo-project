"""Fetch NASA NeoWs asteroid data for a date range and upsert it into PostgreSQL.

Usage::

    python pipeline.py                                  # today only
    python pipeline.py --start-date 2024-01-01          # a single day
    python pipeline.py --start-date 2024-01-01 --end-date 2024-01-20
    python pipeline.py --start-date 2024-01-01 --dry-run   # fetch + validate, no DB writes

Long ranges are fetched in 7-day windows (the NeoWs limit). Each window is
written in its own transaction, so an interrupted run keeps completed windows,
and re-running any range is safe because the upsert is idempotent.

Exit codes: 0 = success, 1 = failure (API, validation, configuration or database error).
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date

import psycopg
from dotenv import load_dotenv
from pydantic import ValidationError

from client import NasaApiError, NeoWsClient
from database import AsteroidRepository, connect
from models import parse_feed

logger = logging.getLogger("pipeline")


@dataclass
class PipelineStats:
    """Running totals across all fetched windows."""

    fetched: int = 0
    invalid: int = 0
    unique: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0


def run(start_date: date, end_date: date, *, dry_run: bool = False) -> PipelineStats:
    """Fetch, validate and store asteroids for the inclusive range ``start_date``..``end_date``.

    Raises:
        ValueError: Invalid date range or missing configuration.
        NasaApiError: The API failed after retries.
        pydantic.ValidationError: The API response envelope was malformed.
        psycopg.Error: A database error occurred.
    """
    if end_date < start_date:
        raise ValueError(f"--end-date ({end_date}) is before --start-date ({start_date})")

    stats = PipelineStats()
    logger.info("Starting pipeline for %s to %s%s", start_date, end_date, " (dry run)" if dry_run else "")

    with NeoWsClient() as client, (nullcontext(None) if dry_run else connect()) as conn:
        repo = AsteroidRepository(conn) if conn is not None else None

        for chunk_start, chunk_end, payload in client.iter_feed(start_date, end_date):
            result = parse_feed(payload)
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

            if repo is None:
                logger.info(
                    "%s..%s: %d objects, %d valid unique asteroids (not written: dry run)",
                    chunk_start, chunk_end, result.element_count, len(result.records),
                )
                continue

            upsert = repo.upsert_many(result.records)
            stats.inserted += upsert.inserted
            stats.updated += upsert.updated
            stats.unchanged += upsert.unchanged
            logger.info(
                "%s..%s: %d objects -> %d inserted, %d updated, %d unchanged, %d invalid",
                chunk_start, chunk_end, result.element_count,
                upsert.inserted, upsert.updated, upsert.unchanged, len(result.invalid),
            )

        if repo is not None:
            logger.info("Table now holds %d asteroids", repo.count())

    return stats


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load NASA NeoWs asteroid data into PostgreSQL.")
    parser.add_argument(
        "--start-date",
        type=date.fromisoformat,
        help="First day to fetch (YYYY-MM-DD). Defaults to --end-date, or today.",
    )
    parser.add_argument(
        "--end-date",
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
    return parser.parse_args(argv)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.log_level)
    load_dotenv()

    start_date: date = args.start_date or args.end_date or date.today()
    end_date: date = args.end_date or start_date

    try:
        stats = run(start_date, end_date, dry_run=args.dry_run)
    except ValueError as exc:
        # Includes pydantic.ValidationError (a ValueError subclass), so check that first.
        if isinstance(exc, ValidationError):
            logger.error("NeoWs response did not match the expected format: %s", exc)
        else:
            logger.error("Invalid input or configuration: %s", exc)
        return 1
    except NasaApiError as exc:
        logger.error("NASA API error: %s", exc)
        return 1
    except psycopg.Error as exc:
        logger.error("Database error: %s", exc)
        return 1
    except KeyboardInterrupt:
        logger.warning("Interrupted; windows already written were committed")
        return 130

    logger.info(
        "Done: %d fetched, %d unique, %d inserted, %d updated, %d unchanged, %d invalid",
        stats.fetched, stats.unique, stats.inserted, stats.updated, stats.unchanged, stats.invalid,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
