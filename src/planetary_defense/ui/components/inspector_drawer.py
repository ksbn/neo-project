"""Selected asteroid: latest pass, its alerts, its orbit (with where it came from) and its approach history."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

import pandas as pd
import streamlit as st

from planetary_defense.application.dashboard_service import DashboardData
from planetary_defense.domain.errors import NeoError
from planetary_defense.domain.models import ApproachSummary, AsteroidSummary, AsteroidView, OrbitData
from planetary_defense.domain.orbital_math import date_from_jd, orbital_period_days
from planetary_defense.domain.units import au_to_ld, km_to_ld
from planetary_defense.ui.messages import describe, orbit_status
from planetary_defense.ui.state import SELECTED, ensure_selection

HISTORY_COLUMNS = list(ApproachSummary.__dataclass_fields__)


def _choices(view: AsteroidView) -> dict[str, str]:
    """neo_reference_id -> label, for every asteroid in the alerts or the table."""
    names = {a.neo_reference_id: a.name for a in view.alerts}
    names.update({r.neo_reference_id: r.name for r in view.rows})
    return {ref: f"{name} ({ref})" for ref, name in names.items()}


def orbit_table(orbit: OrbitData) -> pd.DataFrame:
    el = orbit.elements
    a, e = el.semi_major_axis_au, el.eccentricity
    rows = [
        ("Orbit class", orbit.orbit_class or "—"),
        ("Semi-major axis", f"{a:.4f} AU"),
        ("Eccentricity", f"{e:.4f}"),
        ("Inclination", f"{el.inclination_deg:.3f}°"),
        ("Perihelion / aphelion", f"{a * (1 - e):.3f} / {a * (1 + e):.3f} AU"),
        ("Orbital period", f"{orbital_period_days(a):.1f} days"),
        ("Earth MOID", f"{orbit.moid_au:.5f} AU ({au_to_ld(orbit.moid_au):.2f} LD)" if orbit.moid_au is not None
         else "—"),
        ("Elements epoch", date_from_jd(el.epoch_jd).isoformat()),
        ("NASA solution", orbit.orbit_id or "—"),
    ]
    return pd.DataFrame(rows, columns=["Element", "Value"])


def _render_pass(row: AsteroidSummary | None) -> None:
    if row is None:
        st.caption("This asteroid is only in the alerts, not in the loaded rows.")
        return
    when, miss, speed, size = st.columns(4)
    when.metric("Latest pass", row.close_approach_date.isoformat() if row.close_approach_date else "—")
    miss.metric("Miss distance", f"{km_to_ld(row.miss_distance_km):.2f} LD" if row.miss_distance_km else "—",
                help=f"{row.miss_distance_km:,.0f} km" if row.miss_distance_km else None)
    speed.metric("Velocity", f"{row.relative_velocity_km_s:.1f} km/s" if row.relative_velocity_km_s else "—")
    size.metric("Diameter", f"≤ {row.estimated_diameter_max_km:.2f} km" if row.estimated_diameter_max_km else "—")


def render(view: AsteroidView, data: DashboardData) -> str | None:
    """Draws the inspector and returns the inspected asteroid's id."""
    st.subheader("Inspector")
    choices = _choices(view)
    if not choices:
        st.caption("Nothing to inspect yet.")
        return None
    ensure_selection(view, list(choices))
    neo_id = st.selectbox("Asteroid", list(choices), key=SELECTED, format_func=choices.__getitem__)
    if neo_id is None:
        return None

    row = next((r for r in view.rows if r.neo_reference_id == neo_id), None)
    if row is not None and row.is_potentially_hazardous:
        st.markdown(":red[**Potentially hazardous asteroid**]")
    _render_pass(row)

    own_alerts = [a for a in view.alerts if a.neo_reference_id == neo_id]
    for alert in own_alerts:
        st.markdown(f"- **{alert.severity.name.title()}** ({alert.close_approach_date}): {alert.message}")

    st.markdown("**Orbit**")
    try:
        lookup = data.orbit(neo_id)
    except NeoError as exc:
        st.info(f"Orbit unavailable: {describe(exc)}")
    else:
        st.caption(orbit_status(lookup, datetime.now(UTC)))
        st.dataframe(orbit_table(lookup.orbit), hide_index=True, width="stretch")

    st.markdown("**Approach history**")
    if not view.history_available:
        st.caption("History is only available from the database, not from the live NASA fallback.")
        return neo_id
    try:
        history = data.approach_history(neo_id)
    except NeoError as exc:
        st.caption(f"History unavailable: {describe(exc)}")
        return neo_id
    st.caption(f"{len(history)} recorded approach{'es' if len(history) != 1 else ''}, newest first")
    st.dataframe(
        pd.DataFrame([asdict(r) for r in history], columns=HISTORY_COLUMNS),
        width="stretch",
        hide_index=True,
        column_config={
            "close_approach_date": st.column_config.DateColumn("Close approach"),
            "orbiting_body": "Body",
            "relative_velocity_km_s": st.column_config.NumberColumn("Velocity (km/s)", format="%.3f"),
            "miss_distance_km": st.column_config.NumberColumn("Miss distance (km)", format="%,.0f"),
        },
    )
    return neo_id
