"""Structural interfaces the application services depend on.

These are ``typing.Protocol``s, so the concrete adapters (``NeoWsClient``,
``AsteroidRepository``) satisfy them without inheriting anything, and tests
can pass small fakes.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from database import UpsertResult
    from domain import AsteroidRecord
    from models import ParseResult


class FeedSource(Protocol):
    """Produces validated records for one window of at most 7 days."""

    def fetch_records(self, start_date: date, end_date: date) -> ParseResult: ...


class AsteroidStore(Protocol):
    """Persists records idempotently."""

    def upsert_many(self, records: Sequence[AsteroidRecord]) -> UpsertResult: ...

    def count(self) -> int: ...
