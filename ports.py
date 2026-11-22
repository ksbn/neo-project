"""Structural interfaces between the application services and the adapters.

These are ``typing.Protocol``s, so the concrete adapters (``NeoWsClient``,
``AsteroidRepository``) and the test fakes satisfy them without inheriting
anything. Pyright checks the conformance in CI (see ``tests/test_ports.py``).
Only ``domain`` types appear here, so no port depends on an adapter module.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Protocol

from domain import AsteroidSummary, FeedBatch, Observation, Overview, UpsertResult


class FeedSource(Protocol):
    """Produces validated observations for one window."""

    @property
    def max_window_days(self) -> int:
        """Longest window ``fetch_records`` accepts."""
        ...

    def fetch_records(self, start_date: date, end_date: date) -> FeedBatch: ...


class AsteroidStore(Protocol):
    """Persists observations idempotently (write side)."""

    def save(self, observations: Sequence[Observation]) -> UpsertResult: ...

    def count(self) -> int: ...


class AsteroidReader(Protocol):
    """Answers the dashboard's questions (read side)."""

    def overview(self) -> Overview: ...

    def list_recent(self, limit: int) -> list[AsteroidSummary]: ...
