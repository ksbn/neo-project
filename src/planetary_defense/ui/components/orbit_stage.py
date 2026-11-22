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
from planetary_defense.application.enrich_orbit import earth_view_from
from planetary_defense.application.viewport import pass_payload
from planetary_defense.domain.errors import NeoError
from planetary_defense.domain.models import ApproachSummary, AsteroidSummary, AsteroidView, OrbitLookup, OrbitOrigin
from planetary_defense.domain.units import au_to_ld, km_to_ld
from planetary_defense.ui.components import orbital_3d
from planetary_defense.ui.messages import describe, orbit_status
from planetary_defense.ui.state import SELECTED, ensure_selection
from planetary_defense.ui.theme import PALETTE

VIEWPORT_HEIGHT = 470
MAX_CONTEXT_ARCS = 5
HISTORY_COLUMNS = list(ApproachSummary.__dataclass_fields__)


def context_arcs(
    view: AsteroidView, selected: str, orbits: dict[str, OrbitLookup], limit: int = MAX_CONTEXT_ARCS
) -> list[tuple[str, bool, Any]]:
    """Approach arcs of other asteroids whose orbits are cached: alert asteroids first, then the closest passes."""
    rows = {r.neo_reference_id: r for r in view.rows}
    arcs = []
    for ref in context_candidates(view, selected):
        row, lookup = rows.get(ref), orbits.get(ref)
        if lookup is None or row is None or row.close_approach_date is None or not row.miss_distance_km:
            continue
        trajectory = earth_view_from(lookup, row.close_approach_date, km_to_ld(row.miss_distance_km),
                                     window_days=4).trajectory
        arcs.append((row.name, row.is_potentially_hazardous, trajectory))
        if len(arcs) == limit:
            break
    return arcs


def context_candidates(view: AsteroidView, selected: str, limit: int = 12) -> list[str]:
    by_alert = [a.neo_reference_id for a in view.alerts]
    by_distance = [r.neo_reference_id for r in sorted(
        (r for r in view.rows if r.miss_distance_km), key=lambda r: r.miss_distance_km or 0.0)]
    ordered = [ref for ref in dict.fromkeys(by_alert + by_distance) if ref != selected]
    return ordered[:limit]


def viewport_payload(
    lookup: OrbitLookup | None,
    name: str,
    row: AsteroidSummary | None,
    provenance: str,
    today: date,
    context: list[tuple[str, bool, Any]] | None = None,
) -> dict[str, Any] | None:
    """What the viewport draws for a table row (see :func:`application.viewport.pass_payload`)."""
    approach = None
    if row is not None and row.close_approach_date is not None and row.miss_distance_km:
        if lookup is not None or row.relative_velocity_km_s is not None:
            approach = ApproachSummary(row.close_approach_date, "Earth", row.relative_velocity_km_s or 0.0,
                                       row.miss_distance_km)
    return pass_payload(lookup, name=name, hazardous=row is not None and row.is_potentially_hazardous,
                        approach=approach, provenance=provenance, today=today, context=context)


def _row(key: str, value: str) -> str:
    return f'<div class="row"><span>{key}</span><span>{value}</span></div>'


def _dot(color: str) -> str:
    return f'<span class="astraea-dot" style="color:{color};background:{color}"></span>'


def telemetry_html(
    neo_id: str, name: str, row: AsteroidSummary | None, lookup: OrbitLookup | None, today: date | None = None
) -> str:
    """The readouts: a name tile and two columns of key/value rows. NASA-derived text is escaped."""
    el = lookup.orbit.elements if lookup else None
    upcoming = lookup.orbit.next_approach(today or date.today()) if lookup else None
    next_pass = (f"{upcoming.close_approach_date:%Y-%m-%d} · {km_to_ld(upcoming.miss_distance_km):.2f} LD"
                 if upcoming else "none listed to 2200" if lookup and lookup.orbit.approaches is not None else "—")
    orbit_class = html.escape(lookup.orbit.orbit_class) if lookup and lookup.orbit.orbit_class else "—"
    moid = lookup.orbit.moid_au if lookup else None
    dash = "—"
    if row is not None and row.estimated_diameter_max_km:
        low = row.estimated_diameter_min_km or row.estimated_diameter_max_km
        diameter = f"{low * 1000:,.0f}–{row.estimated_diameter_max_km * 1000:,.0f} m"
    else:
        diameter = dash
    miss = (f"{km_to_ld(row.miss_distance_km):.2f} LD · {row.miss_distance_km:,.0f} km"
            if row is not None and row.miss_distance_km else dash)
    left = [
        _row("Object ID", html.escape(neo_id)),
        _row("Inclination (deg)", f"{el.inclination_deg:.3f}°" if el else dash),
        _row("Eccentricity", f"{el.eccentricity:.4f}" if el else dash),
        _row("Diameter (est.)", diameter),
        _row("Next Earth Pass (predicted)", next_pass),
    ]
    right = [
        _row("Semi-Major Axis (AU)", f"{el.semi_major_axis_au:.4f}" if el else dash),
        _row("Absolute Magnitude", f"{row.absolute_magnitude_h:.2f}" if row is not None and row.absolute_magnitude_h
             else dash),
        _row("Minimum Earth Distance (LD/km)", miss),
        _row("Earth MOID (LD)", f"{au_to_ld(moid):.2f}" if moid is not None else dash),
        _row("Orbit Class", orbit_class),
    ]
    return (f'<div class="astraea-telemetry"><div class="name">{html.escape(name)}</div>'
            f'<div class="rows">{"".join(left)}</div><div class="rows">{"".join(right)}</div></div>')


def status_html(view: AsteroidView, lookup: OrbitLookup | None, orbit_error: str | None) -> str:
    """The footer status bar: Live Feed / Data Synced / orbit source (with error states)."""
    feed_ok = view.history_available
    synced = view.status.last_success_at if view.status else None
    if orbit_error:
        orbit_state, orbit_color = "Orbit unavailable", PALETTE["critical"]
    elif lookup is not None and lookup.origin is OrbitOrigin.STALE_CACHE:
        orbit_state, orbit_color = "Orbit stale", PALETTE["watch"]
    else:
        origin = lookup.origin.value.replace("_", " ") if lookup else "none"
        orbit_state, orbit_color = f"Orbit: {origin}", PALETTE["nominal"]
    synced_text = f"Data Synced {synced.astimezone(UTC):%H:%M} UTC" if synced else "Not synced"
    return (
        '<div class="astraea-footer">'
        f"<span>{_dot(PALETTE['nominal'] if feed_ok else PALETTE['watch'])}"
        f"{'Live Feed' if feed_ok else 'NASA Fallback'}</span>"
        f"<span>⟳ {synced_text}</span>"
        f"<span>{_dot(orbit_color)}{orbit_state}</span></div>"
    )


def _choices(view: AsteroidView) -> dict[str, str]:
    names = {a.neo_reference_id: a.name for a in view.alerts}
    names.update({p.neo_reference_id: p.name for p in view.upcoming})
    names.update({r.neo_reference_id: r.name for r in view.rows})
    return names


def render(view: AsteroidView, data: DashboardData, today: date | None = None) -> str | None:
    """Draws the viewport and telemetry panels and returns the tracked asteroid's id."""
    names = _choices(view)
    if not names:
        st.caption("Nothing to track yet.")
        return None
    ensure_selection(view, list(names))
    now = datetime.now(UTC)
    today = today or now.date()

    with st.container(border=True):
        title, picker = st.columns([2, 3], vertical_alignment="center")
        with title:
            st.html('<div class="astraea-panel-title" style="border:0;margin:0">3D Space Viewport</div>')
        with picker:
            neo_id = st.selectbox("Tracking", list(names), key=SELECTED, label_visibility="collapsed",
                                  format_func=lambda ref: f"Tracking: {names[ref]} ({ref})")
        if neo_id is None:
            return None
        row = next((r for r in view.rows if r.neo_reference_id == neo_id), None)

        lookup: OrbitLookup | None = None
        orbit_error: str | None = None
        try:
            lookup = data.orbit(neo_id)
        except NeoError as exc:
            orbit_error = describe(exc)
        provenance = orbit_status(lookup, now) if lookup else f"Orbit unavailable: {orbit_error}"
        context = context_arcs(view, neo_id, data.cached_orbits(context_candidates(view, neo_id)))

        payload = viewport_payload(lookup, names[neo_id], row, provenance, today, context=context)
        if payload is not None:
            orbital_3d.render(payload, height=VIEWPORT_HEIGHT)
        if orbit_error:
            fallback = " Showing an approximate straight-line pass instead." if payload else ""
            st.warning(f"Orbit unavailable: {orbit_error}{fallback}")
        elif lookup is not None and lookup.note:
            st.caption(lookup.note)

    with st.container(border=True):
        st.html('<div class="astraea-panel-title">Orbital Telemetry Readouts <span class="chev">⌄</span></div>')
        st.html(telemetry_html(neo_id, names[neo_id], row, lookup, today))

    st.html(status_html(view, lookup, orbit_error))
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
