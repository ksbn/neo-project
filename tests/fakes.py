"""In-memory fakes for the ports in ``application/ports.py``, shared by the tests.

Pyright checks that every fake still matches its Protocol (``tests/application/test_ports.py``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime

from planetary_defense.application.dashboard_service import LoadResult, SyncReport
from planetary_defense.domain.errors import OrbitUnavailable, StorageError, UpstreamUnavailable
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
    OrbitLookup,
    OrbitOrigin,
    Overview,
    PipelineStatus,
    RunStatus,
    Severity,
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

    def daily_approaches(self, since: date) -> list[tuple[date, int]]:
        return [(since, 2)]


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


class StubAlerts:
    """AlertReader returning fixed alerts (or failing like a missing anomaly_alerts table)."""

    def __init__(self, alerts: list[AnomalyAlert] | None = None, fail: bool = False) -> None:
        self.alerts = alerts or []
        self.fail = fail

    def recent_alerts(self, min_severity: Severity, limit: int) -> list[AnomalyAlert]:
        if self.fail:
            raise StorageError('relation "anomaly_alerts" does not exist')
        return [a for a in self.alerts if a.severity >= min_severity][:limit]


# --------------------------------------------------------------------------- #
# The dashboard as the page sees it
# --------------------------------------------------------------------------- #


def control_room_view(*, live: bool = False) -> AsteroidView:
    """Three asteroids: Zoozve with a warning, 'b' with a watch alert, 'a' quiet but hazardous."""
    rows = [
        AsteroidSummary("2524522", "524522 Zoozve (2002 VE68)", 0.2, 0.45, True, date(2026, 11, 2), 6_019_000.0, 9.5),
        AsteroidSummary("b", "(2019 BG3)", 0.05, 0.1, False, date(2026, 11, 20), 9_000_000.0, 36.9),
        AsteroidSummary("a", "(2001 KF54)", 0.3, 0.7, True, date(2026, 11, 18), 20_000_000.0, 12.0),
    ]
    alerts = [
        AnomalyAlert("hazardous_close_pass", Severity.WARNING, "2524522", rows[0].name, date(2026, 11, 2),
                     "Potentially hazardous asteroid passing close: Passes 15.65 LD from Earth, within 20 LD"),
        AnomalyAlert("high_velocity", Severity.WATCH, "b", rows[1].name, date(2026, 11, 20),
                     "Relative velocity 36.9 km/s (at least 30 km/s)", 36.9, 30.0),
        AnomalyAlert("potentially_hazardous", Severity.INFO, "a", rows[2].name, date(2026, 11, 18),
                     "Classified by NASA as a potentially hazardous asteroid"),
    ]
    overview = Overview(total=3, hazardous=2, latest_approach=date(2026, 11, 20), approaches=4,
                        closest_miss_ld=15.65, fastest_km_s=36.9)
    trend = [(date(2026, 11, d), n) for d, n in ((16, 3), (17, 5), (18, 4), (19, 7), (20, 6))]
    if live:
        return AsteroidView(overview=overview, rows=rows, alerts=alerts, trend=trend)
    status = PipelineStatus(last_run=LAST_RUN, last_success_at=LAST_RUN.finished_at)
    return AsteroidView(overview=overview, rows=rows, status=status, history_available=True, alerts=alerts,
                        trend=trend)


class FakeDashboard:
    """:class:`application.dashboard_service.DashboardData` in memory."""

    def __init__(self, *, live: bool = False, empty: bool = False, orbit_fails: bool = False) -> None:
        self.view = None if empty else control_room_view(live=live)
        self.orbit_fails = orbit_fails
        self.refreshed = 0

    def load(self) -> LoadResult:
        if self.view is None:
            return LoadResult(None, None, ["PostgreSQL database: unavailable", "NASA: unavailable"])
        return LoadResult(self.view, "NASA NeoWs API (live fallback)" if not self.view.history_available
                          else "PostgreSQL database", [])

    def approach_history(self, neo_reference_id: str) -> list[ApproachSummary]:
        return [ApproachSummary(date(2026, 11, 2), "Earth", 9.5, 6_019_000.0),
                ApproachSummary(date(2002, 11, 8), "Earth", 10.1, 8_000_000.0)]

    def orbit(self, neo_reference_id: str) -> OrbitLookup:
        if self.orbit_fails:
            raise OrbitUnavailable(f"No orbit for {neo_reference_id}: NASA could not be reached and none is cached")
        return OrbitLookup(replace(zoozve_orbit(), neo_reference_id=neo_reference_id),
                           datetime(2026, 11, 20, 8, 0, tzinfo=UTC), OrbitOrigin.CACHE)

    def refresh(self) -> None:
        self.refreshed += 1

    def sync(self) -> SyncReport:
        return SyncReport(ok=False, message="Sync did not complete because of NASA being unavailable.")
