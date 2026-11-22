"""Data access for the Streamlit dashboard, kept free of Streamlit so it can be unit-tested.

The dashboard reads PostgreSQL first and falls back to the live NASA API. The
order is data (:class:`FallbackReader` takes a list of :class:`Source`), not
hard-coded control flow. Only :class:`errors.NeoError` triggers the fallback;
any other exception is a bug and propagates.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Final

import pandas as pd

from client import INTERACTIVE, NeoWsClient
from config import Settings
from database import SUMMARY_COLUMNS, AsteroidRepository, connect
from errors import NeoError

logger = logging.getLogger(__name__)

COLUMNS: Final = list(SUMMARY_COLUMNS)
NUMERIC_COLUMNS: Final = ["estimated_diameter_min_km", "estimated_diameter_max_km", "miss_distance_km"]
MAX_ROWS: Final = 5_000
# Live fallback covers today and the 2 days before it, matching scripts/daily_pipeline.sh.
API_FALLBACK_DAYS: Final = 2


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Give both sources the same columns and dtypes (Postgres NUMERIC arrives as Decimal)."""
    df = df.reindex(columns=COLUMNS)
    for col in NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["is_potentially_hazardous"] = df["is_potentially_hazardous"].astype("boolean").fillna(False).astype(bool)
    df["close_approach_date"] = pd.to_datetime(df["close_approach_date"], errors="coerce").dt.date  # pyright: ignore[reportAttributeAccessIssue]
    return df


def read_database(settings: Settings, limit: int = MAX_ROWS) -> pd.DataFrame:
    """Newest asteroids from PostgreSQL. Raises ConfigError or StorageError."""
    with connect(settings.db(), connect_timeout=3, application_name="neo-dashboard") as conn:
        rows = AsteroidRepository(conn).list_recent(limit)
    return normalize(pd.DataFrame(rows, columns=COLUMNS))


def read_nasa(settings: Settings, today: date | None = None) -> pd.DataFrame:
    """The last few days live from NeoWs, with the interactive retry profile. Raises ConfigError or UpstreamError."""
    end = today or date.today()
    start = end - timedelta(days=API_FALLBACK_DAYS)
    with NeoWsClient(settings.nasa_api_key(), profile=INTERACTIVE) as client:
        result = client.fetch_records(start, end)
    df = pd.DataFrame([r.model_dump(include=set(COLUMNS)) for r in result.records], columns=COLUMNS)
    return normalize(df).sort_values("close_approach_date", ascending=False, na_position="last")


@dataclass(frozen=True)
class Source:
    """One place the dashboard can read from."""

    label: str
    read: Callable[[], pd.DataFrame]
    empty_hint: str = "returned no asteroids"


@dataclass(frozen=True)
class LoadResult:
    data: pd.DataFrame
    source: str | None
    diagnostics: list[str] = field(default_factory=list)


class FallbackReader:
    """Try each source in order; return the first non-empty result."""

    def __init__(self, *sources: Source) -> None:
        self._sources = sources

    def load(self) -> LoadResult:
        diagnostics: list[str] = []
        for src in self._sources:
            try:
                df = src.read()
            except NeoError as exc:
                logger.warning("%s failed: %s", src.label, exc)  # full detail stays in the server log
                diagnostics.append(f"{src.label}: {exc.user_message}")
                continue
            if not df.empty:
                return LoadResult(df, src.label, diagnostics)
            diagnostics.append(f"{src.label}: {src.empty_hint}")
        return LoadResult(normalize(pd.DataFrame(columns=COLUMNS)), None, diagnostics)
