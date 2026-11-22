"""Streamlit dashboard: composition root and rendering.

This is the only dashboard module that knows the concrete adapters
(PostgreSQL, the NASA client), Streamlit and pandas. Data access logic lives in
``dashboard_data.py``; error wording lives in ``ui_messages.py``.
"""

from dataclasses import asdict
from datetime import date

import pandas as pd
import streamlit as st  # pyright: ignore[reportMissingImports]

from client import INTERACTIVE, NeoWsClient
from config import Settings
from dashboard_data import MAX_ROWS, FallbackReader, LoadResult, Source, view_from_feed, view_from_store
from database import AsteroidRepository, connect
from domain import AsteroidSummary, AsteroidView
from resilience import CircuitBreaker
from ui_messages import describe

COLUMNS = [f for f in AsteroidSummary.__dataclass_fields__]
DB_COOLDOWN_S = 30
NASA_COOLDOWN_S = 120


@st.cache_resource
def get_settings() -> Settings:
    return Settings.load()


@st.cache_resource
def breakers() -> dict[str, CircuitBreaker]:
    """Shared by all sessions: one failure skips the source for everyone until the cooldown ends."""
    return {"db": CircuitBreaker(DB_COOLDOWN_S), "nasa": CircuitBreaker(NASA_COOLDOWN_S)}


# A source that raises is not cached by Streamlit, so the page recovers as soon
# as the source is back; the breakers stop it from retrying on every rerun.
@st.cache_data(ttl=300, show_spinner="Loading asteroids from PostgreSQL...")
def database_view() -> AsteroidView:
    with connect(get_settings().db(), connect_timeout=3, application_name="neo-dashboard") as conn:
        return view_from_store(AsteroidRepository(conn), MAX_ROWS)


@st.cache_data(ttl=60, show_spinner="Fetching live data from NASA...")
def nasa_view() -> AsteroidView:
    key = get_settings().nasa_api_key()
    with NeoWsClient(key, profile=INTERACTIVE, user_agent="neo-dashboard/1.0") as client:
        return view_from_feed(client, date.today())


def load() -> LoadResult:
    guard = breakers()
    return FallbackReader(
        Source(
            "PostgreSQL database",
            lambda: guard["db"].call(database_view),
            empty_hint="the asteroids table is empty (run pipeline.py)",
        ),
        Source(
            "NASA NeoWs API (live fallback)",
            lambda: guard["nasa"].call(nasa_view),
            empty_hint="returned no asteroids for the last few days",
        ),
        describe=describe,
    ).load()


def refresh() -> None:
    database_view.clear()
    nasa_view.clear()
    get_settings.clear()
    for breaker in breakers().values():
        breaker.reset()


def to_frame(rows: list[AsteroidSummary]) -> pd.DataFrame:
    """The one place rows become a DataFrame."""
    return pd.DataFrame([asdict(r) for r in rows], columns=COLUMNS)


def render_sidebar(result: LoadResult) -> None:
    with st.sidebar:
        st.header("Data source")
        if result.source:
            st.success(result.source)
        if st.button("🔄 Refresh data"):
            refresh()
            st.rerun()
        if result.diagnostics:
            with st.expander("Diagnostics", expanded=result.source is None):
                for msg in result.diagnostics:
                    st.caption(msg)


def render_overview(view: AsteroidView) -> None:
    overview = view.overview
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Asteroids Tracked", overview.total)
    col2.metric("Potentially Hazardous", overview.hazardous)
    col3.metric("Latest Approach Date", str(overview.latest_approach) if overview.latest_approach else "—")


def render_table(view: AsteroidView) -> None:
    st.subheader("📋 Asteroid Records")

    search_query = st.text_input("Search asteroid by name:", "")
    hazardous_only = st.checkbox("Show only potentially hazardous asteroids")

    df = to_frame(view.rows)
    if search_query:
        df = df[df["name"].str.contains(search_query, case=False, na=False, regex=False)]
    if hazardous_only:
        df = df[df["is_potentially_hazardous"]]

    loaded = len(view.rows)
    scope = f"the newest {loaded} of {view.overview.total}" if loaded < view.overview.total else f"{loaded}"
    st.caption(f"Showing {len(df)} of {scope} asteroids")
    st.dataframe(
        df,
        width="stretch",
        hide_index=True,
        column_config={
            "neo_reference_id": "NEO ID",
            "name": "Name",
            "estimated_diameter_min_km": st.column_config.NumberColumn("Min diameter (km)", format="%.3f"),
            "estimated_diameter_max_km": st.column_config.NumberColumn("Max diameter (km)", format="%.3f"),
            "is_potentially_hazardous": st.column_config.CheckboxColumn("Hazardous"),
            "close_approach_date": st.column_config.DateColumn("Close approach"),
            "miss_distance_km": st.column_config.NumberColumn("Miss distance (km)", format="%,.0f"),
        },
    )


def main() -> None:
    st.set_page_config(page_title="NASA NeoWs Dashboard", page_icon="🌠", layout="wide")
    st.title("NASA Near Earth Objects (NeoWs) Dashboard")
    st.markdown("Live data pipeline tracker powered by Python, PostgreSQL, and NASA's NeoWs API.")

    result = load()
    render_sidebar(result)

    if result.view is None:
        st.warning(
            "Unable to fetch asteroid data from the database or the NASA API right now. "
            "Check the Diagnostics panel in the sidebar for details."
        )
        st.stop()
        return

    render_overview(result.view)
    render_table(result.view)


main()
