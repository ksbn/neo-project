"""Upcoming Approaches: NASA/JPL's predicted Earth passes of the asteroids whose orbits are cached.

Each pass is scored by the same anomaly rules as recorded ones, so a close or
hazardous pass is flagged before it happens. Only asteroids with a cached orbit
are covered (the pipeline caches the orbits of every asteroid it ingests); the
caption says how many. The ▸ button makes the asteroid the tracked one.
"""

from __future__ import annotations

import html
from datetime import date, timedelta
from typing import Final

import streamlit as st

from planetary_defense.domain.models import AnomalyAlert, AsteroidView, Severity, UpcomingApproach
from planetary_defense.domain.units import km_to_ld
from planetary_defense.ui.components.hazard_alerts import LEVEL_NAMES, RULE_NAMES
from planetary_defense.ui.state import select
from planetary_defense.ui.theme import PALETTE, SEVERITY_COLORS

MAX_ROWS: Final = 6
HORIZONS: Final = {"Next 30 days": 30, "Next year": 365, "Next 10 years": 3_653}
DEFAULT_HORIZON: Final = "Next year"
HORIZON_KEY: Final = "upcoming_horizon"
ALERTED_KEY: Final = "upcoming_alerted_only"


def worst_alerts(
    alerts: list[AnomalyAlert], min_severity: Severity = Severity.WATCH
) -> dict[tuple[str, date], AnomalyAlert]:
    """The most severe alert (at least ``min_severity``) of each predicted pass, by (asteroid, date)."""
    worst: dict[tuple[str, date], AnomalyAlert] = {}
    for a in alerts:
        if a.severity < min_severity or a.close_approach_date is None:
            continue
        key = (a.neo_reference_id, a.close_approach_date)
        if key not in worst or a.severity > worst[key].severity:
            worst[key] = a
    return worst


def countdown(day: date, today: date) -> str:
    days = (day - today).days
    if days == 0:
        return "today"
    if days < 60:
        return f"in {days} d"
    if days < 730:
        return f"in {days / 30.44:.0f} mo"
    return f"in {days / 365.25:.1f} y"


def pass_row(p: UpcomingApproach, alert: AnomalyAlert | None, today: date) -> str:
    """One predicted pass: severity (or nominal), object, date and countdown, size, miss distance."""
    color = SEVERITY_COLORS[alert.severity] if alert else PALETTE["nominal"]
    label = f"{alert.severity.name}: " if alert else ""
    why = (f"{LEVEL_NAMES[alert.severity]} - {RULE_NAMES.get(alert.rule, alert.rule.replace('_', ' ').capitalize())}"
           if alert else ("Potentially hazardous · nominal pass" if p.is_potentially_hazardous else "Nominal pass"))
    a = p.approach
    size = f"≤{p.estimated_diameter_max_km * 1000:,.0f} m" if p.estimated_diameter_max_km else "—"
    miss_ld = km_to_ld(a.miss_distance_km)
    tooltip = alert.message if alert else f"{miss_ld:.2f} LD at {a.relative_velocity_km_s:.1f} km/s"
    return (
        f'<div class="astraea-alert upcoming" style="border-left-color:{color}" title="{html.escape(tooltip)}">'
        f'<span class="icon" style="color:{color}">{"⚠︎" if alert else "◷"}</span>'
        f'<div><div class="label" style="color:{color}">{label}{html.escape(p.name)}</div>'
        f'<div class="why">({html.escape(why)})</div></div>'
        f'<div class="cell">{countdown(a.close_approach_date, today)}<b>{a.close_approach_date:%Y-%m-%d}</b></div>'
        f'<div class="cell">Size<b>{size}</b></div>'
        f'<div class="cell">Miss<b>{miss_ld:.1f} LD</b></div></div>'
    )


def render(view: AsteroidView, today: date | None = None) -> None:
    today = today or date.today()
    st.html('<div class="astraea-panel-title">Upcoming Approaches · predicted by NASA/JPL '
            '<span class="chev">⌃</span></div>')
    if not view.history_available:
        st.caption("Predicted passes come from the database's orbit cache, which is unavailable right now.")
        return

    horizon_col, alerted_col = st.columns([3, 2], vertical_alignment="center")
    horizon = horizon_col.selectbox("Horizon", list(HORIZONS), index=list(HORIZONS).index(DEFAULT_HORIZON),
                                    key=HORIZON_KEY, label_visibility="collapsed")
    alerted_only = alerted_col.checkbox("With alerts only", key=ALERTED_KEY)

    until = today + timedelta(days=HORIZONS[horizon])
    worst = worst_alerts(view.upcoming_alerts)
    passes = [p for p in view.upcoming if today <= p.approach.close_approach_date <= until]
    if alerted_only:
        passes = [p for p in passes if (p.neo_reference_id, p.approach.close_approach_date) in worst]

    covered = len({p.neo_reference_id for p in view.upcoming})
    if not passes:
        later = next((p for p in view.upcoming if p.approach.close_approach_date > until), None)
        hint = (f" Next: {later.name} on {later.approach.close_approach_date:%Y-%m-%d}." if later and not alerted_only
                else "")
        st.caption(f"No {'alerted ' if alerted_only else ''}predicted passes {horizon.lower()}.{hint}"
                   if view.upcoming else
                   "No predicted passes yet: they arrive as the pipeline caches orbits (NASA /neo/{id}).")
        return

    for i, p in enumerate(passes[:MAX_ROWS]):
        line, action = st.columns([14, 1], vertical_alignment="center", gap="small")
        with line:
            st.html(pass_row(p, worst.get((p.neo_reference_id, p.approach.close_approach_date)), today))
        with action:
            st.button("▸", key=f"upcoming_{i}_{p.neo_reference_id}", on_click=select, args=(p.neo_reference_id,),
                      help=f"Track {p.name}")
    more = f" (+ {len(passes) - MAX_ROWS} more)" if len(passes) > MAX_ROWS else ""
    st.caption(f"{len(passes)} predicted Earth passes {horizon.lower()}{more} · "
               f"covers the {covered} asteroids with a cached orbit and a pass in the next 10 years")
