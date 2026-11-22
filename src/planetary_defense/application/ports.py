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

from planetary_defense.domain.models import (
    ApproachSummary,
    AsteroidSummary,
    FeedBatch,
    IngestionRun,
    Observation,
    Overview,
    PipelineStatus,
    UpsertResult,
)


class FeedSource(Protocol):
    """Produces validated observations for one window."""

    @property
    def max_window_days(self) -> int:
        """Longest window ``fetch_records`` accepts."""
        ...

    def fetch_records(self, start_date: date, end_date: date) -> FeedBatch: ...


class AsteroidStore(Protocol):
    """Persists observations idempotently (write side): the newest per asteroid, and every approach as history."""

    def save(self, observations: Sequence[Observation]) -> UpsertResult: ...

    def count(self) -> int: ...


class AsteroidReader(Protocol):
    """Answers the dashboard's questions (read side)."""

    def overview(self) -> Overview: ...

    def list_recent(self, limit: int) -> list[AsteroidSummary]: ...

    def approach_history(self, neo_reference_id: str, limit: int) -> list[ApproachSummary]: ...


class RunLog(Protocol):
    """Keeps a record of pipeline runs (write side)."""

    def record(self, run: IngestionRun) -> None: ...


class RunReader(Protocol):
    """Answers "when did the pipeline last run, and did it work?" (read side)."""

    def pipeline_status(self) -> PipelineStatus: ...
