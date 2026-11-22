"""Close Approach Data Table: compact, tier-coloured by distance. Selecting a row tracks the asteroid."""

from __future__ import annotations

from typing import Literal

import pandas as pd
import streamlit as st

from planetary_defense.domain.models import AsteroidSummary, AsteroidView
from planetary_defense.domain.units import km_to_ld
from planetary_defense.ui.state import on_row_selected
from planetary_defense.ui.theme import PALETTE

TABLE_KEY = "approach_table"
DISTANCE = "Distance (LD)"
Tier = Literal["critical", "warning", "nominal"]


def distance_tier(ld: float | None, critical_ld: float = 1.0, warning_ld: float = 5.0) -> Tier | None:
    """< 1 LD critical, 1-5 LD warning, otherwise nominal."""
    if ld is None:
        return None
    return "critical" if ld < critical_ld else "warning" if ld <= warning_ld else "nominal"


def to_frame(rows: list[AsteroidSummary]) -> pd.DataFrame:
    """The table's rows, in display units (LD, metres)."""
    return pd.DataFrame({
        "neo_reference_id": [r.neo_reference_id for r in rows],
        "Object Name": [r.name for r in rows],
        "Date (UTC)": [r.close_approach_date for r in rows],
        DISTANCE: [km_to_ld(r.miss_distance_km) if r.miss_distance_km is not None else None for r in rows],
        "Velocity (km/s)": [r.relative_velocity_km_s for r in rows],
        "Diameter (m)": [r.estimated_diameter_max_km * 1000 if r.estimated_diameter_max_km else None for r in rows],
        "hazardous": [r.is_potentially_hazardous for r in rows],
    })


def _tier_style(ld: float | None) -> str:
    tier = distance_tier(ld)
    if tier == "critical":
        return f"color: {PALETTE['critical']}; font-weight: 700"
    if tier == "warning":
        return f"color: {PALETTE['warning']}; font-weight: 600"
    return f"color: {PALETTE['nominal']}" if tier else ""


def render(view: AsteroidView) -> pd.DataFrame:
    """Draws the table and returns the rows left after filtering."""
    st.subheader("Close Approach Data")
    search, hazardous = st.columns([3, 2], vertical_alignment="bottom")
    query = search.text_input("Search by name", "", placeholder="Search by name", label_visibility="collapsed")
    hazardous_only = hazardous.checkbox("Hazardous only")

    df = to_frame(view.rows)
    if query:
        df = df.loc[df["Object Name"].str.contains(query, case=False, na=False, regex=False)]
    if hazardous_only:
        df = df.loc[df["hazardous"]]
    df = df.reset_index(drop=True)

    styled = (
        df.style.map(_tier_style, subset=[DISTANCE])
        .format({DISTANCE: "{:.2f}", "Velocity (km/s)": "{:.1f}", "Diameter (m)": "{:,.0f}"}, na_rep="—")
    )
    st.dataframe(
        styled,
        hide_index=True,
        width="stretch",
        height=380,
        key=TABLE_KEY,
        on_select=on_row_selected(TABLE_KEY, list(df["neo_reference_id"])),
        selection_mode="single-row",
        column_order=["Object Name", "Date (UTC)", DISTANCE, "Velocity (km/s)", "Diameter (m)"],
    )
    loaded = len(view.rows)
    scope = f"newest {loaded:,} of {view.overview.total:,}" if loaded < view.overview.total else f"{loaded:,}"
    st.caption(f"{len(df):,} of {scope} objects · < 1 LD critical · 1–5 LD warning · select a row to track it")
    return df
