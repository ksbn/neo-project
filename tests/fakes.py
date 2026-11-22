"""In-memory fakes for the ports in ``application/ports.py``, shared by the tests.

Pyright checks that every fake still matches its Protocol (``tests/application/test_ports.py``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime

from planetary_defense.domain.errors import StorageError, UpstreamUnavailable
from planetary_defense.domain.models import (
    AnomalyAlert,
    ApproachSummary,
    AsteroidSummary,
    AsteroidView,
    CachedOrbit,
    FeedBatch,
    IngestionRun,
    Observation,
    OrbitalElements,
    OrbitData,
    Overview,
    PipelineStatus,
    RunStatus,
    UpsertResult,
    latest,
)
from planetary_defense.infrastructure.nasa_dto import parse_feed

from support import make_feed, make_neo


class FakeFeed:
    """Serves one asteroid per requested day; fails on windows starting on a date in ``fail_on``."""

    max_window_days = 7

    def __init__(self, fail_on: frozenset[date] = frozenset()) -> None:
        self.fail_on = fail_on
        self.calls: list[tuple[date, date]] = []

    def fetch_records(self, start_date: date, end_date: date) -> FeedBatch:
        self.calls.append((start_date, end_date))
        if start_date in self.fail_on:
            raise UpstreamUnavailable("NASA is down")
        days = [date.fromordinal(d).isoformat() for d in range(start_date.toordinal(), end_date.toordinal() + 1)]
        return parse_feed(make_feed({d: [make_neo(d.replace("-", ""), d)] for d in days}))


class InMemoryStore:
    def __init__(self, fail: bool = False) -> None:
        self.rows: dict[str, Observation] = {}
        self.fail = fail

    def save(self, observations: Sequence[Observation]) -> UpsertResult:
        if self.fail:
            raise StorageError("disk full")
        newest = latest(observations)
        inserted = sum(o.neo_reference_id not in self.rows for o in newest)
        self.rows.update({o.neo_reference_id: o for o in newest})
        return UpsertResult(inserted=inserted, unchanged=len(newest) - inserted, new_approaches=len(observations))

    def count(self) -> int:
        return len(self.rows)


class FakeRunLog:
    def __init__(self, fail: bool = False) -> None:
        self.runs: list[IngestionRun] = []
        self.fail = fail

    def record(self, run: IngestionRun) -> None:
        if self.fail:
            raise StorageError("connection lost")
        self.runs.append(run)


def view(*names: str) -> AsteroidView:
    rows = [AsteroidSummary(n, n, None, None, False, None, None) for n in names]
    return AsteroidView(Overview(total=len(rows), hazardous=0, latest_approach=None), rows)


class StubReader:
    def __init__(self) -> None:
        self.limits: list[int] = []

    def overview(self) -> Overview:
        return Overview(total=10_000, hazardous=42, latest_approach=date(2026, 11, 22))

    def list_recent(self, limit: int) -> list[AsteroidSummary]:
        self.limits.append(limit)
        return view("a").rows

    def approach_history(self, neo_reference_id: str, limit: int) -> list[ApproachSummary]:
        return []


LAST_RUN = IngestionRun(
    started_at=datetime(2026, 11, 22, 7, 59, tzinfo=UTC), finished_at=datetime(2026, 11, 22, 8, 0, tzinfo=UTC),
    start_date=date(2026, 11, 20), end_date=date(2026, 11, 22), status=RunStatus.SUCCESS,
)


class StubRuns:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def pipeline_status(self) -> PipelineStatus:
        if self.fail:
            raise StorageError("relation ingestion_runs does not exist")
        return PipelineStatus(last_run=LAST_RUN, last_success_at=LAST_RUN.finished_at)


class StubFeed:
    max_window_days = 7

    def __init__(self, batch: FeedBatch) -> None:
        self.batch = batch
        self.calls: list[tuple[date, date]] = []

    def fetch_records(self, start_date: date, end_date: date) -> FeedBatch:
        self.calls.append((start_date, end_date))
        return self.batch


class RunLogSpy:
    def __init__(self) -> None:
        self.runs: list[IngestionRun] = []

    def record(self, run: IngestionRun) -> None:
        self.runs.append(run)


# --------------------------------------------------------------------------- #
# Orbit enrichment
# --------------------------------------------------------------------------- #


def zoozve_orbit() -> OrbitData:
    """Zoozve's real elements (see support.ZOOZVE_ORBITAL_DATA)."""
    return OrbitData(
        neo_reference_id="2524522",
        elements=OrbitalElements(
            semi_major_axis_au=0.72356318914361, eccentricity=0.4101013489730946, inclination_deg=9.037338276017495,
            ascending_node_deg=231.4572421200886, perihelion_argument_deg=355.3998989862241,
            mean_anomaly_deg=288.8364601767468, epoch_jd=2461200.5,
        ),
        orbit_id="111",
        orbit_class="ATE",
        moid_au=0.025508,
    )


class FakeOrbitSource:
    """NASA's /neo/{id}: returns Zoozve's orbit (re-labelled with the requested id), or raises ``fail``."""

    def __init__(self, fail: Exception | None = None) -> None:
        self.fail = fail
        self.calls: list[str] = []

    def fetch_orbit(self, neo_reference_id: str) -> OrbitData:
        self.calls.append(neo_reference_id)
        if self.fail is not None:
            raise self.fail
        return replace(zoozve_orbit(), neo_reference_id=neo_reference_id)


class InMemoryOrbitCache:
    def __init__(self, fail_get: Exception | None = None, fail_put: Exception | None = None) -> None:
        self.orbits: dict[str, CachedOrbit] = {}
        self.fail_get, self.fail_put = fail_get, fail_put

    def get_orbit(self, neo_reference_id: str) -> CachedOrbit | None:
        if self.fail_get is not None:
            raise self.fail_get
        return self.orbits.get(neo_reference_id)

    def put_orbit(self, orbit: CachedOrbit) -> None:
        if self.fail_put is not None:
            raise self.fail_put
        self.orbits[orbit.orbit.neo_reference_id] = orbit


class AlertStoreSpy:
    def __init__(self, fail: Exception | None = None) -> None:
        self.calls: list[tuple[list[tuple[str, date]], list[AnomalyAlert]]] = []
        self.fail = fail

    def replace_alerts(self, passes: Sequence[tuple[str, date]], alerts: Sequence[AnomalyAlert]) -> None:
        if self.fail is not None:
            raise self.fail
        self.calls.append((list(passes), list(alerts)))
