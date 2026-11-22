"""Anomaly alert feed, most urgent first. Selecting an alert inspects its asteroid."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from planetary_defense.domain.models import AnomalyAlert, AsteroidView, Severity
from planetary_defense.ui.state import on_row_selected

TABLE_KEY = "alerts_table"
COLUMNS = ["severity", "name", "close_approach_date", "message"]


def to_frame(alerts: list[AnomalyAlert]) -> pd.DataFrame:
    return pd.DataFrame(
        [[a.severity.name.title(), a.name, a.close_approach_date, a.message] for a in alerts], columns=COLUMNS
    )


def render(view: AsteroidView) -> None:
    st.subheader("Alerts")
    levels = list(Severity)
    floor = st.selectbox(
        "Show alerts from", levels, index=levels.index(Severity.WATCH), key="alert_floor",
        format_func=lambda s: f"{s.name.title()} and up",
    )
    shown = [a for a in view.alerts if a.severity >= floor]
    if not shown:
        st.caption(f"No {floor.name.lower()}-or-worse alerts." if view.alerts else "No alerts.")
        return
    st.dataframe(
        to_frame(shown),
        hide_index=True,
        width="stretch",
        key=TABLE_KEY,
        on_select=on_row_selected(TABLE_KEY, [a.neo_reference_id for a in shown]),
        selection_mode="single-row",
        column_config={
            "severity": "Severity",
            "name": "Asteroid",
            "close_approach_date": st.column_config.DateColumn("Pass"),
            "message": st.column_config.TextColumn("Why", width="large"),
        },
    )
