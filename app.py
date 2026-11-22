"""Streamlit dashboard: rendering only. Data access lives in dashboard_data.py."""

import pandas as pd
import streamlit as st  # pyright: ignore[reportMissingImports]

from config import Settings
from dashboard_data import FallbackReader, LoadResult, Source, read_database, read_nasa


@st.cache_resource
def get_settings() -> Settings:
    return Settings.load()


# Each source is cached on its own. A source that raises is not cached by
# Streamlit, so the page recovers as soon as the database or NASA is back.
@st.cache_data(ttl=300, show_spinner="Loading asteroids from PostgreSQL...")
def cached_database() -> pd.DataFrame:
    return read_database(get_settings())


@st.cache_data(ttl=60, show_spinner="Fetching live data from NASA...")
def cached_nasa() -> pd.DataFrame:
    return read_nasa(get_settings())


def load() -> LoadResult:
    return FallbackReader(
        Source("PostgreSQL database", cached_database, empty_hint="the asteroids table is empty (run pipeline.py)"),
        Source("NASA NeoWs API (live fallback)", cached_nasa, empty_hint="returned no asteroids for the last few days"),
    ).load()


def refresh() -> None:
    cached_database.clear()
    cached_nasa.clear()
    get_settings.clear()


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


def render_table(df: pd.DataFrame) -> None:
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Asteroids Tracked", len(df))
    col2.metric("Potentially Hazardous", int(df["is_potentially_hazardous"].sum()))
    latest = df["close_approach_date"].max()
    col3.metric("Latest Approach Date", str(latest) if pd.notna(latest) else "—")

    st.subheader("📋 Asteroid Records")

    search_query = st.text_input("Search asteroid by name:", "")
    hazardous_only = st.checkbox("Show only potentially hazardous asteroids")

    view = df
    if search_query:
        view = view[view["name"].str.contains(search_query, case=False, na=False, regex=False)]
    if hazardous_only:
        view = view[view["is_potentially_hazardous"]]

    st.caption(f"Showing {len(view)} of {len(df)} asteroids")
    st.dataframe(
        view,
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

    if result.data.empty:
        st.warning(
            "Unable to fetch asteroid data from the database or the NASA API right now. "
            "Check the Diagnostics panel in the sidebar for details."
        )
        st.stop()

    render_table(result.data)


main()
