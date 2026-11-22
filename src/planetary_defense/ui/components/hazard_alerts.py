"""Hazard Alerts: the most urgent anomaly alerts as cards. "Track" makes the asteroid the selected one."""

from __future__ import annotations

import html
from datetime import date

import streamlit as st

from planetary_defense.domain.models import AnomalyAlert, AsteroidSummary, AsteroidView, Severity
from planetary_defense.ui.state import select
from planetary_defense.ui.theme import SEVERITY_COLORS, badge

MAX_CARDS = 6


def when(day: date | None, today: date) -> str:
    if day is None:
        return "no recorded pass"
    delta = (day - today).days
    relative = "today" if delta == 0 else f"in {delta} days" if delta > 0 else f"{-delta} days ago"
    return f"{day.isoformat()} UTC · {relative}"


def alert_card(alert: AnomalyAlert, row: AsteroidSummary | None, today: date) -> str:
    """HTML for one alert card; every NASA-derived string is escaped."""
    color = SEVERITY_COLORS[alert.severity]
    size = (f" · ≤ {row.estimated_diameter_max_km * 1000:,.0f} m"
            if row is not None and row.estimated_diameter_max_km else "")
    return (
        f'<div class="astraea-alert" style="border-left-color:{color}">'
        f'{badge(alert.severity.name, color)} <span class="name">{html.escape(alert.name)}</span>'
        f'<div class="meta">{when(alert.close_approach_date, today)}{size}</div>'
        f'<div class="meta">{html.escape(alert.message)}</div></div>'
    )


def render(view: AsteroidView, today: date | None = None) -> None:
    today = today or date.today()
    st.subheader("Hazard Alerts")
    levels = list(Severity)
    floor = st.selectbox(
        "Show", levels, index=levels.index(Severity.WATCH), key="alert_floor",
        format_func=lambda s: f"{s.name.title()} and up", label_visibility="collapsed",
    )
    shown = [a for a in view.alerts if a.severity >= floor]
    if not shown:
        st.caption(f"No {floor.name.lower()}-or-worse alerts." if view.alerts else "No alerts.")
        return
    rows = {r.neo_reference_id: r for r in view.rows}
    for i, alert in enumerate(shown[:MAX_CARDS]):
        card, action = st.columns([4, 1], vertical_alignment="center")
        with card:
            st.html(alert_card(alert, rows.get(alert.neo_reference_id), today))
        with action:
            st.button("Track", key=f"track_{i}_{alert.neo_reference_id}_{alert.rule}",
                      on_click=select, args=(alert.neo_reference_id,), help=f"Inspect {alert.name}")
    if len(shown) > MAX_CARDS:
        st.caption(f"+ {len(shown) - MAX_CARDS} more {floor.name.lower()}-or-worse alerts")
