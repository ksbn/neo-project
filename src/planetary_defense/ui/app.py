"""Planetary Defense Control Room: composition root (``LiveDashboard``) and page layout (``render``).

``LiveDashboard`` is the only dashboard code that knows the concrete adapters
(PostgreSQL, the NASA client), Streamlit's caches and the circuit breakers.
``render`` draws the page from any :class:`application.dashboard_service.DashboardData`,
so tests can render it with an in-memory fake. Components live in ``ui/components/``.
Started by the root ``app.py``.
"""

from datetime import UTC, date, datetime, timedelta

import streamlit as st

from planetary_defense.application.dashboard_service import (
    MAX_ROWS,
    DashboardData,
    FallbackReader,
    LoadResult,
    Source,
    SyncReport,
    view_from_feed,
    view_from_store,
)
from planetary_defense.application.enrich_orbit import OrbitEnricher
from planetary_defense.config import Settings
from planetary_defense.domain.anomaly_rules import default_rules
from planetary_defense.domain.errors import StorageError, UpstreamError
from planetary_defense.domain.models import ApproachSummary, AsteroidView, CachedOrbit, OrbitLookup
from planetary_defense.infrastructure.db import connect
from planetary_defense.infrastructure.error_boundary import CircuitBreaker, GuardedOrbitSource
from planetary_defense.infrastructure.nasa_client import INTERACTIVE, NeoWsClient
from planetary_defense.infrastructure.repository import (
    AlertRepository,
    AsteroidRepository,
    IngestionRunRepository,
    OrbitRepository,
)
from planetary_defense.ui import theme
from planetary_defense.ui.components import approach_table, command_header, hazard_alerts, kpi_strip, orbit_stage
from planetary_defense.ui.messages import describe, run_health, sync_message

DB_COOLDOWN_S = 30
NASA_COOLDOWN_S = 120
HISTORY_LIMIT = 100
USER_AGENT = "neo-dashboard/1.0"


# --------------------------------------------------------------------------- #
# Live data: caches, breakers and adapters
# --------------------------------------------------------------------------- #


@st.cache_resource
def get_settings() -> Settings:
    return Settings.load()


@st.cache_resource
def breakers() -> dict[str, CircuitBreaker]:
    """Shared by all sessions: one failure skips the source for everyone until the cooldown ends."""
    return {
        "db": CircuitBreaker(DB_COOLDOWN_S),
        "nasa": CircuitBreaker(NASA_COOLDOWN_S),
        # Orbit lookups trip only on outages: one asteroid without an orbit must not block the others.
        "nasa_orbits": CircuitBreaker(NASA_COOLDOWN_S, trips_on=(UpstreamError,)),
    }


def _connect():
    return connect(get_settings().db(), connect_timeout=3, application_name="neo-dashboard")


# A function that raises is not cached by Streamlit, so the page recovers as soon
# as a source is back; the breakers stop it from retrying on every rerun.
@st.cache_data(ttl=300, show_spinner="Loading the control room from PostgreSQL...")
def database_view() -> AsteroidView:
    with _connect() as conn:
        return view_from_store(AsteroidRepository(conn), IngestionRunRepository(conn), AlertRepository(conn), MAX_ROWS)


@st.cache_data(ttl=60, show_spinner="Fetching live data from NASA...")
def nasa_view() -> AsteroidView:
    settings = get_settings()
    with NeoWsClient(settings.nasa_api_key(), profile=INTERACTIVE, user_agent=USER_AGENT) as client:
        return view_from_feed(client, date.today(), rules=default_rules(settings.alert_thresholds()))


@st.cache_data(ttl=300, show_spinner=False)
def approach_history(neo_reference_id: str) -> list[ApproachSummary]:
    with _connect() as conn:
        return AsteroidRepository(conn).approach_history(neo_reference_id, HISTORY_LIMIT)


class _NoOrbitCache:
    """Stands in for the orbit cache when the database is down, so lookups still reach NASA."""

    def __init__(self, error: StorageError) -> None:
        self._error = error

    def get_orbit(self, neo_reference_id: str) -> CachedOrbit | None:
        raise self._error

    def put_orbit(self, orbit: CachedOrbit) -> None:
        raise self._error


@st.cache_data(ttl=600, show_spinner="Looking up the orbit...")
def orbit_lookup(neo_reference_id: str) -> OrbitLookup:
    settings = get_settings()
    with NeoWsClient(settings.nasa_api_key(), profile=INTERACTIVE, user_agent=USER_AGENT) as client:
        source = GuardedOrbitSource(client, breakers()["nasa_orbits"])
        try:
            conn = _connect()
        except StorageError as exc:
            return OrbitEnricher(_NoOrbitCache(exc), source, max_age=settings.orbit_cache_max_age()).lookup(
                neo_reference_id
            )
        with conn:
            enricher = OrbitEnricher(OrbitRepository(conn), source, max_age=settings.orbit_cache_max_age())
            return enricher.lookup(neo_reference_id)


class LiveDashboard:
    """:class:`DashboardData` over PostgreSQL first and NASA as the fallback."""

    def load(self) -> LoadResult:
        guard = breakers()
        return FallbackReader(
            Source(
                "PostgreSQL database",
                lambda: guard["db"].call(database_view),
                empty_hint="the asteroids table is empty (run scripts/run_pipeline.sh)",
            ),
            Source(
                "NASA NeoWs API (live fallback)",
                lambda: guard["nasa"].call(nasa_view),
                empty_hint="returned no asteroids for the last few days",
            ),
            describe=describe,
        ).load()

    def approach_history(self, neo_reference_id: str) -> list[ApproachSummary]:
        return breakers()["db"].call(lambda: approach_history(neo_reference_id))

    def orbit(self, neo_reference_id: str) -> OrbitLookup:
        return orbit_lookup(neo_reference_id)

    def sync(self) -> SyncReport:
        """Run the pipeline for the last 3 days, exactly as the scheduled job does, with quick NASA retries."""
        from planetary_defense.cli import main as run_pipeline  # the batch entry point, reused as-is

        end = date.today()
        start = end - timedelta(days=2)
        code = run_pipeline(
            ["--start", start.isoformat(), "--end", end.isoformat(), "--no-warm-orbits"],
            settings=get_settings(),
            open_feed=lambda s: NeoWsClient(s.nasa_api_key(), profile=INTERACTIVE, user_agent=USER_AGENT),
        )
        self.refresh()
        ok, message = sync_message(int(code), start, end)
        return SyncReport(ok=ok, message=message)

    def refresh(self) -> None:
        for cached in (database_view, nasa_view, approach_history, orbit_lookup):
            cached.clear()
        get_settings.clear()
        for breaker in breakers().values():
            breaker.reset()


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #


def render_sidebar(result: LoadResult, data: DashboardData) -> None:
    with st.sidebar:
        st.header("Data source")
        if result.source:
            st.success(result.source)
        if st.button("Refresh data"):
            data.refresh()
            st.rerun()
        if result.diagnostics:
            with st.expander("Diagnostics", expanded=result.source is None):
                for msg in result.diagnostics:
                    st.caption(msg)


def render_status(view: AsteroidView) -> None:
    """A banner only when something needs attention; the readiness card shows the healthy state."""
    if not view.history_available:
        st.info("Showing live NASA data: the database is unavailable, so alerts are scored on the fly.")
        return
    health = run_health(view.status, datetime.now(UTC))
    if health.level != "success":
        getattr(st, health.level)(health.text)


def render(data: DashboardData) -> None:
    st.set_page_config(page_title="ASTRAEA: Planetary Defense Command", page_icon="🛰️", layout="wide")
    theme.apply()

    result = data.load()
    command_header.render(result, data)
    render_sidebar(result, data)
    if result.view is None:
        st.warning(
            "Unable to fetch asteroid data from the database or the NASA API right now. "
            "Check the Diagnostics panel in the sidebar for details."
        )
        return

    view = result.view
    kpi_strip.render(view)
    render_status(view)
    left, right = st.columns([5, 6], gap="medium")
    with left:
        hazard_alerts.render(view)
        approach_table.render(view)
    with right:
        orbit_stage.render(view, data)


def main() -> None:
    render(LiveDashboard())
