"""Right-hand stage: 3D Space Viewport for the tracked asteroid and its Orbital Telemetry Readouts.

The orbit comes from ``DashboardData.orbit`` (cache first, NASA ``/neo/{id}`` only on a miss).
Without an orbit (missing fields, rate limit, offline) the viewport falls back to a straight-line
pass at NASA's miss distance and speed, clearly labelled, and the rest of the page is unaffected.
"""

from __future__ import annotations

import html
from dataclasses import asdict
from datetime import UTC, date, datetime
from typing import Any

import pandas as pd
import streamlit as st

from planetary_defense.application.dashboard_service import DashboardData
from planetary_defense.application.enrich_orbit import earth_view_from, sun_view_from
from planetary_defense.domain.errors import NeoError
from planetary_defense.domain.models import ApproachSummary, AsteroidSummary, AsteroidView, OrbitLookup, OrbitOrigin
from planetary_defense.domain.orbital_math import straight_line_flyby
from planetary_defense.domain.units import au_to_ld, km_to_ld
from planetary_defense.ui.components import orbital_3d
from planetary_defense.ui.messages import describe, orbit_status
from planetary_defense.ui.state import SELECTED, ensure_selection
from planetary_defense.ui.theme import PALETTE

VIEWPORT_HEIGHT = 560
HISTORY_COLUMNS = list(ApproachSummary.__dataclass_fields__)


def viewport_payload(
    lookup: OrbitLookup | None, name: str, row: AsteroidSummary | None, provenance: str, today: date
) -> dict[str, Any] | None:
    """What the viewport draws, or None when there is nothing to draw.

    With an orbit: the Earth view of the latest pass (if any) and the Sun view on that date.
    Without one: an approximate straight-line pass from NASA's miss distance and speed.
    """
    pass_date = row.close_approach_date if row is not None else None
    miss_km = row.miss_distance_km if row is not None else None
    hazardous = row is not None and row.is_potentially_hazardous
    if lookup is not None:
        on = pass_date if pass_date is not None and miss_km else today
        trajectory = earth_view_from(lookup, on, km_to_ld(miss_km)).trajectory if pass_date and miss_km else None
        return orbital_3d.build_payload(name=name, hazardous=hazardous, provenance=provenance,
                                        trajectory=trajectory, sun=sun_view_from(lookup, on))
    if pass_date is not None and miss_km and row is not None and row.relative_velocity_km_s is not None:
        trajectory = straight_line_flyby(pass_date, km_to_ld(miss_km), row.relative_velocity_km_s)
        return orbital_3d.build_payload(name=name, hazardous=hazardous, provenance=provenance,
                                        trajectory=trajectory, sun=None, approximate=True)
    return None


def _readout(key: str, value: str) -> str:
    return f'<div class="astraea-readout"><div class="k">{key}</div><div class="v">{value}</div></div>'


def _dot(color: str) -> str:
    return f'<span class="astraea-dot" style="color:{color};background:{color}"></span>'


def telemetry_html(
    neo_id: str, name: str, row: AsteroidSummary | None, lookup: OrbitLookup | None, view: AsteroidView,
    orbit_error: str | None,
) -> str:
    """The readouts strip and status line. Every NASA-derived string is escaped."""
    el = lookup.orbit.elements if lookup else None
    moid = lookup.orbit.moid_au if lookup else None
    dash = "—"
    if row is not None and row.estimated_diameter_max_km:
        low = row.estimated_diameter_min_km or row.estimated_diameter_max_km
        diameter = f"{low * 1000:,.0f}–{row.estimated_diameter_max_km * 1000:,.0f} m"
    else:
        diameter = dash
    miss = (f"{km_to_ld(row.miss_distance_km):.2f} LD · {row.miss_distance_km:,.0f} km"
            if row is not None and row.miss_distance_km else dash)
    readouts = [
        _readout("Object", f"{html.escape(name)}<br><span style='color:{PALETTE['muted']}'>ID {html.escape(neo_id)}"
                           "</span>"),
        _readout("Inclination", f"{el.inclination_deg:.3f}°" if el else dash),
        _readout("Eccentricity", f"{el.eccentricity:.4f}" if el else dash),
        _readout("Semi-major axis", f"{el.semi_major_axis_au:.4f} AU" if el else dash),
        _readout("Abs. magnitude H",
                 f"{row.absolute_magnitude_h:.2f}" if row is not None and row.absolute_magnitude_h else dash),
        _readout("Diameter (est.)", diameter),
        _readout("Min. Earth distance", miss),
        _readout("Earth MOID", f"{au_to_ld(moid):.2f} LD" if moid is not None else dash),
    ]

    feed_ok = view.history_available
    synced = view.status.last_success_at if view.status else None
    if orbit_error:
        orbit_state, orbit_color = "Orbit unavailable", PALETTE["critical"]
    elif lookup is not None and lookup.origin is OrbitOrigin.STALE_CACHE:
        orbit_state, orbit_color = "Orbit stale", PALETTE["watch"]
    else:
        origin = lookup.origin.value.replace("_", " ") if lookup else "none"
        orbit_state, orbit_color = f"Orbit: {origin}", PALETTE["nominal"]
    status = (
        f'<b>{_dot(PALETTE["nominal"] if feed_ok else PALETTE["watch"])}'
        f'{"Live Feed" if feed_ok else "NASA Fallback"}</b>'
        f'<b>{_dot(PALETTE["nominal"] if synced else PALETTE["watch"])}'
        f'{"Data Synced " + f"{synced.astimezone(UTC):%H:%M} UTC" if synced else "Not synced"}</b>'
        f"<b>{_dot(orbit_color)}{orbit_state}</b>"
    )
    return f'<div class="astraea-readouts">{"".join(readouts)}</div><div class="astraea-status">{status}</div>'


def _choices(view: AsteroidView) -> dict[str, str]:
    names = {a.neo_reference_id: a.name for a in view.alerts}
    names.update({r.neo_reference_id: r.name for r in view.rows})
    return names


def render(view: AsteroidView, data: DashboardData) -> str | None:
    """Draws the stage and returns the tracked asteroid's id."""
    st.subheader("3D Space Viewport")
    names = _choices(view)
    if not names:
        st.caption("Nothing to track yet.")
        return None
    ensure_selection(view, list(names))
    neo_id = st.selectbox("Tracking", list(names), key=SELECTED, label_visibility="collapsed",
                          format_func=lambda ref: f"Tracking: {names[ref]} ({ref})")
    if neo_id is None:
        return None
    row = next((r for r in view.rows if r.neo_reference_id == neo_id), None)
    now = datetime.now(UTC)

    lookup: OrbitLookup | None = None
    orbit_error: str | None = None
    try:
        lookup = data.orbit(neo_id)
    except NeoError as exc:
        orbit_error = describe(exc)
    provenance = orbit_status(lookup, now) if lookup else f"Orbit unavailable: {orbit_error}"

    payload = viewport_payload(lookup, names[neo_id], row, provenance, now.date())
    if payload is not None:
        orbital_3d.render(payload, height=VIEWPORT_HEIGHT)
    if orbit_error:
        fallback = " Showing an approximate straight-line pass instead." if payload else ""
        st.warning(f"Orbit unavailable: {orbit_error}{fallback}")
    elif lookup is not None and lookup.note:
        st.caption(lookup.note)

    st.html(telemetry_html(neo_id, names[neo_id], row, lookup, view, orbit_error))

    with st.expander("Alerts & approach history"):
        for alert in (a for a in view.alerts if a.neo_reference_id == neo_id):
            st.markdown(f"- **{alert.severity.name.title()}** ({alert.close_approach_date}): {alert.message}")
        _render_history(view, data, neo_id)
    return neo_id


def _render_history(view: AsteroidView, data: DashboardData, neo_id: str) -> None:
    if not view.history_available:
        st.caption("History is only available from the database, not from the live NASA fallback.")
        return
    try:
        history = data.approach_history(neo_id)
    except NeoError as exc:
        st.caption(f"History unavailable: {describe(exc)}")
        return
    st.caption(f"{len(history)} recorded approach{'es' if len(history) != 1 else ''}, newest first")
    st.dataframe(
        pd.DataFrame([asdict(r) for r in history], columns=HISTORY_COLUMNS),
        width="stretch", hide_index=True,
        column_config={
            "close_approach_date": st.column_config.DateColumn("Close approach (UTC)"),
            "orbiting_body": "Body",
            "relative_velocity_km_s": st.column_config.NumberColumn("Velocity (km/s)", format="%.2f"),
            "miss_distance_km": st.column_config.NumberColumn("Miss distance (km)", format="%,.0f"),
        },
    )
