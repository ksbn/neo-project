"""Top KPI cards: tracked asteroids, hazardous, closest pass, fastest pass, open alerts."""

from __future__ import annotations

import streamlit as st

from planetary_defense.domain.models import AsteroidView, Severity


def render(view: AsteroidView) -> None:
    o = view.overview
    urgent = sum(a.severity >= Severity.WARNING for a in view.alerts)
    tracked, hazardous, closest, fastest, alerts = st.columns(5)
    tracked.metric("Tracked NEOs", f"{o.total:,}", help=f"{o.approaches:,} close approaches recorded")
    hazardous.metric("Potentially Hazardous", f"{o.hazardous:,}")
    closest.metric(
        "Closest Pass", f"{o.closest_miss_ld:.2f} LD" if o.closest_miss_ld is not None else "—",
        help="Closest recorded approach, in lunar distances (1 LD = 1/389 AU, NASA's convention)",
    )
    fastest.metric("Fastest Pass", f"{o.fastest_km_s:.1f} km/s" if o.fastest_km_s is not None else "—")
    alerts.metric("Warnings & Critical", f"{urgent:,}", help=f"{len(view.alerts):,} alerts in total")
