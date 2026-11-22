"""Hazard Alerts rows (top of the left panel). The ▸ button makes the asteroid the tracked one."""

from __future__ import annotations

import html
from datetime import date
from typing import Final

import streamlit as st

from planetary_defense.domain.models import AnomalyAlert, AsteroidSummary, AsteroidView, Severity
from planetary_defense.domain.units import km_to_ld
from planetary_defense.ui.state import select
from planetary_defense.ui.theme import SEVERITY_COLORS

MAX_ROWS: Final = 5
LEVEL_NAMES: Final = {Severity.INFO: "Advisory", Severity.WATCH: "Guarded", Severity.WARNING: "Elevated",
                      Severity.CRITICAL: "Severe"}
RULE_NAMES: Final = {"close_approach": "Close pass", "hazardous_close_pass": "Hazardous close pass",
                     "high_velocity": "High velocity", "large_diameter": "Large object",
                     "potentially_hazardous": "Potentially hazardous"}


def alert_row(alert: AnomalyAlert, row: AsteroidSummary | None) -> str:
    """One alert as a row: icon, severity and object, pass time, estimated size, miss distance.

    Every NASA-derived string is escaped; the full rule message is the row's tooltip.
    """
    color = SEVERITY_COLORS[alert.severity]
    icon = "ⓘ" if alert.severity is Severity.INFO else "⚠︎"
    rule = RULE_NAMES.get(alert.rule, alert.rule.replace("_", " ").capitalize())
    day = alert.close_approach_date
    size = f"≤{row.estimated_diameter_max_km * 1000:,.0f} m" if row and row.estimated_diameter_max_km else "—"
    miss = f"{km_to_ld(row.miss_distance_km):.1f} LD" if row and row.miss_distance_km else "—"
    return (
        f'<div class="astraea-alert" style="border-left-color:{color}" title="{html.escape(alert.message)}">'
        f'<span class="icon" style="color:{color}">{icon}</span>'
        f'<div><div class="label" style="color:{color}">{alert.severity.name}: {html.escape(alert.name)}</div>'
        f'<div class="why">({LEVEL_NAMES[alert.severity]} - {html.escape(rule)})</div></div>'
        f'<div class="cell">Time<b>{f"{day:%m-%d}" if day else "—"}</b>UTC</div>'
        f'<div class="cell">Size<b>{size}</b></div>'
        f'<div class="cell">Miss<b>{miss}</b></div></div>'
    )


def render(view: AsteroidView, today: date | None = None) -> None:
    head, floor_col = st.columns([3, 1], vertical_alignment="center")
    with head:
        st.html('<div class="astraea-subtitle"><span>Hazard Alerts</span><span>Time</span>'
                "<span>Estimated Size</span><span>Miss</span></div>")
    with floor_col:
        levels = list(Severity)
        floor = st.selectbox("Show alerts from", levels, index=levels.index(Severity.WATCH), key="alert_floor",
                             format_func=lambda s: f"{s.name.title()}+", label_visibility="collapsed")
    shown = [a for a in view.alerts if a.severity >= floor]
    if not shown:
        st.caption(f"No {floor.name.lower()}-or-worse alerts." if view.alerts else "No alerts.")
        return
    rows = {r.neo_reference_id: r for r in view.rows}
    for i, alert in enumerate(shown[:MAX_ROWS]):
        line, action = st.columns([14, 1], vertical_alignment="center", gap="small")
        with line:
            st.html(alert_row(alert, rows.get(alert.neo_reference_id)))
        with action:
            st.button("▸", key=f"track_{i}_{alert.neo_reference_id}_{alert.rule}", on_click=select,
                      args=(alert.neo_reference_id,), help=f"Track {alert.name}")
    if len(shown) > MAX_ROWS:
        st.caption(f"+ {len(shown) - MAX_ROWS} more {floor.name.lower()}-or-worse alerts")
