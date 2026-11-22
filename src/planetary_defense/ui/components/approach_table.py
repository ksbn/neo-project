"""Filterable table of the newest approaches. Selecting a row inspects its asteroid."""

from __future__ import annotations

from dataclasses import asdict

import pandas as pd
import streamlit as st

from planetary_defense.domain.models import AsteroidSummary, AsteroidView
from planetary_defense.domain.units import km_to_ld
from planetary_defense.ui.state import on_row_selected

TABLE_KEY = "approach_table"
COLUMNS = [*AsteroidSummary.__dataclass_fields__, "miss_distance_ld"]


def to_frame(rows: list[AsteroidSummary]) -> pd.DataFrame:
    """The one place approach rows become a DataFrame (adds the distance in LD)."""
    df = pd.DataFrame([asdict(r) for r in rows], columns=COLUMNS[:-1])
    df["miss_distance_ld"] = [km_to_ld(r.miss_distance_km) if r.miss_distance_km is not None else None for r in rows]
    return df


def render(view: AsteroidView) -> pd.DataFrame:
    """Draws the table and returns the rows left after filtering."""
    st.subheader("Close Approaches")
    search, hazardous = st.columns([3, 2])
    query = search.text_input("Search asteroid by name", "")
    hazardous_only = hazardous.checkbox("Only potentially hazardous")

    df = to_frame(view.rows)
    if query:
        df = df.loc[df["name"].str.contains(query, case=False, na=False, regex=False)]
    if hazardous_only:
        df = df.loc[df["is_potentially_hazardous"]]

    loaded = len(view.rows)
    scope = f"the newest {loaded:,} of {view.overview.total:,}" if loaded < view.overview.total else f"{loaded:,}"
    st.caption(f"Showing {len(df):,} of {scope} asteroids. Select a row to inspect it.")
    st.dataframe(
        df,
        hide_index=True,
        width="stretch",
        key=TABLE_KEY,
        on_select=on_row_selected(TABLE_KEY, list(df["neo_reference_id"])),
        selection_mode="single-row",
        column_order=["name", "close_approach_date", "miss_distance_ld", "relative_velocity_km_s",
                      "estimated_diameter_max_km", "is_potentially_hazardous"],
        column_config={
            "name": "Asteroid",
            "close_approach_date": st.column_config.DateColumn("Close approach"),
            "miss_distance_ld": st.column_config.NumberColumn("Miss (LD)", format="%.2f"),
            "relative_velocity_km_s": st.column_config.NumberColumn("Velocity (km/s)", format="%.1f"),
            "estimated_diameter_max_km": st.column_config.NumberColumn("Max diameter (km)", format="%.3f"),
            "is_potentially_hazardous": st.column_config.CheckboxColumn("Hazardous"),
        },
    )
    return df
