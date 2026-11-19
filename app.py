import os
from datetime import date, timedelta

import pandas as pd
import streamlit as st # pyright: ignore[reportMissingImports]
from dotenv import load_dotenv

from client import NeoWsClient
from database import DbConfig, connect
from models import parse_feed

load_dotenv()

st.set_page_config(page_title="NASA NeoWs Dashboard", page_icon="🌠", layout="wide")

st.title("NASA Near Earth Objects (NeoWs) Dashboard")
st.markdown("Live data pipeline tracker powered by Python, PostgreSQL, and NASA's NeoWs API.")

COLUMNS = [
    "neo_reference_id",
    "name",
    "estimated_diameter_min_km",
    "estimated_diameter_max_km",
    "is_potentially_hazardous",
    "close_approach_date",
    "miss_distance_km",
]
NUMERIC_COLUMNS = ["estimated_diameter_min_km", "estimated_diameter_max_km", "miss_distance_km"]


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Give both data sources the same dtypes (Postgres NUMERIC arrives as Decimal objects)."""
    df = df.reindex(columns=COLUMNS)
    for col in NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["is_potentially_hazardous"] = df["is_potentially_hazardous"].fillna(False).astype(bool)
    df["close_approach_date"] = pd.to_datetime(df["close_approach_date"], errors="coerce").dt.date # pyright: ignore[reportAttributeAccessIssue]
    return df


def load_from_db() -> pd.DataFrame:
    # Same settings and validation as the pipeline (raises ValueError if .env is incomplete).
    with connect(DbConfig.from_env(), connect_timeout=3) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT neo_reference_id, name, estimated_diameter_min_km, "
                "estimated_diameter_max_km, is_potentially_hazardous, "
                "close_approach_date, miss_distance_km FROM asteroids;"
            )
            rows = cur.fetchall()
            return pd.DataFrame(rows, columns=COLUMNS)


def load_from_api() -> pd.DataFrame:
    """Fetch the last few days live, through the same client and validation as the pipeline.

    The shared client sends the API key in a header, so error messages shown in
    the Diagnostics panel never contain it.
    """
    end_dt = date.today()
    start_dt = end_dt - timedelta(days=3)
    api_key = os.getenv("NASA_API_KEY") or "DEMO_KEY"
    # Fewer retries and a shorter read timeout than the batch pipeline: someone is waiting on the page.
    with NeoWsClient(api_key=api_key, timeout=(5.0, 15.0), max_retries=2) as client:
        result = parse_feed(client.get_feed(start_dt, end_dt))

    df = pd.DataFrame([record.model_dump(include=set(COLUMNS)) for record in result.records], columns=COLUMNS)
    return df.sort_values("close_approach_date", ascending=False)


@st.cache_data(ttl=300, show_spinner="Loading asteroid data...")
def load_data():
    """Returns (dataframe, source_label, list_of_error_messages)."""
    errors = []
    try:
        df = load_from_db()
        if not df.empty:
            return _normalize(df), "PostgreSQL database", errors
        errors.append("Database is reachable but the `asteroids` table is empty (run pipeline.py).")
    except Exception as e:
        errors.append(f"Database unavailable: {type(e).__name__}: {e}")

    try:
        df = load_from_api()
        if not df.empty:
            return _normalize(df), "NASA NeoWs API (live fallback)", errors
        errors.append("NASA API returned no asteroids for the selected dates.")
    except Exception as e:
        errors.append(f"NASA API request failed: {type(e).__name__}: {e}")

    return pd.DataFrame(columns=COLUMNS), None, errors


df, source, errors = load_data()

with st.sidebar:
    st.header("Data source")
    if source:
        st.success(source)
    if st.button("🔄 Refresh data"):
        load_data.clear()
        st.rerun()
    if errors:
        with st.expander("Diagnostics", expanded=source is None):
            for msg in errors:
                st.caption(msg)

if df.empty:
    st.warning(
        "Unable to fetch asteroid data from the database or the NASA API right now. "
        "Check the Diagnostics panel in the sidebar for details."
    )
    st.stop()

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
