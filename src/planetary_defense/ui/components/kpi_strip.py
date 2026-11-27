"""Top 4-card KPI strip: Threat Level, NEOs (with an approaches-per-day sparkline), Close Approaches (24h),
Defense Readiness.

Glass cards rendered inline with ``st.html`` (not an iframe), so the cards really blur the sky
behind them. Streamlit's sanitiser drops inline SVG, so the sparkline is CSS bars. Every figure
comes from the already-loaded view (stored data), so the strip renders even when a live sync
fails. NASA-derived text is HTML-escaped.
"""

from __future__ import annotations

import html
from datetime import UTC, date, datetime
from typing import Final

import streamlit as st

from planetary_defense.application.dashboard_service import threat_level, window_summary
from planetary_defense.domain.models import AsteroidView, Severity
from planetary_defense.ui.messages import run_health
from planetary_defense.ui.theme import PALETTE

SEGMENTS: Final = (PALETTE["nominal"], PALETTE["accent"], PALETTE["watch"], PALETTE["warning"], PALETTE["critical"])

#: Threat scale 1..5 from the worst nearby alert.
THREAT_SCALE: Final = {
    None: (1, "Low"),
    Severity.INFO: (2, "Advisory"),
    Severity.WATCH: (3, "Guarded"),
    Severity.WARNING: (4, "Elevated"),
    Severity.CRITICAL: (5, "Severe"),
}

READINESS: Final = {
    "success": ("Operational (Green)", PALETTE["nominal"]),
    "warning": ("Operational (Yellow)", PALETTE["watch"]),
    "error": ("Degraded (Red)", PALETTE["critical"]),
    "info": ("Standby (Blue)", PALETTE["accent"]),
}


def threat(view: AsteroidView, today: date) -> tuple[int, str, str]:
    """(level 1-5, name, colour) from the worst alert on passes within +-7 days, recorded or predicted."""
    level, name = THREAT_SCALE[threat_level([*view.alerts, *view.upcoming_alerts], today)]
    return level, name, SEGMENTS[level - 1]


def readiness(view: AsteroidView, now: datetime) -> tuple[str, str, str]:
    """(label, colour, detail) from the pipeline's health."""
    if not view.history_available:
        return "Degraded (Yellow)", PALETTE["watch"], "Database unavailable · live NASA fallback"
    label, color = READINESS[run_health(view.status, now).level]
    last = view.status.last_success_at if view.status else None
    return label, color, f"Last sync: {last.astimezone(UTC):%Y-%m-%d %H:%M} UTC" if last else "No successful sync yet"


def sparkline_bars(values: list[int]) -> str:
    """Approaches per day as CSS bars (heights in % of the busiest day); empty days keep a hairline."""
    if not values:
        return '<div class="astraea-bars" aria-hidden="true"></div>'
    peak = max(values) or 1
    bars = "".join(f'<i style="height:{max(6, round(v / peak * 100))}%" title="{v}"></i>' for v in values)
    return f'<div class="astraea-bars" role="img" aria-label="approaches per day">{bars}</div>'


def _card(label: str, value: str, sub: str = "", extra: str = "", glow: str = PALETTE["accent"]) -> str:
    return (f'<div class="astraea-kpi" style="--glow:{glow}2e"><div class="label">{label}</div>'
            f'<div class="value">{value}</div>{extra}{f"<div class=sub>{sub}</div>" if sub else ""}</div>')


def strip_html(view: AsteroidView, today: date, now: datetime) -> str:
    level, threat_name, threat_color = threat(view, today)
    scale = "".join(
        f'<i style="background:{c};opacity:{1 if i < level else 0.18};'
        f'box-shadow:{f"0 0 10px {c}" if i < level else "none"}"></i>'
        for i, c in enumerate(SEGMENTS)
    )
    window = window_summary(view.rows, today, days=0)
    closest = (f"Closest today {window.closest_ld:.2f} LD · {html.escape(window.closest.name)}"
               if window.closest is not None and window.closest_ld is not None else "No passes recorded today")
    ready_label, ready_color, ready_detail = readiness(view, now)
    ready_name, _, ready_tone = ready_label.partition(" (")
    trend = [n for _, n in view.trend]
    dot = f'<span class="astraea-dot" style="color:{ready_color};background:{ready_color}"></span>'
    cards = [
        _card("Threat Level", f'<span style="color:{threat_color}">{threat_name}</span><small>Level {level}</small>',
              extra=f'<div class="astraea-scale">{scale}</div>', glow=threat_color),
        _card("Near-Earth Objects (NEOs)", f"{view.overview.total:,}<small>tracked</small>",
              f"{view.overview.hazardous:,} potentially hazardous · approaches/day, {len(trend)} days",
              extra=sparkline_bars(trend)),
        _card("Close Approaches (24h)", f'<span style="color:{PALETTE["accent"]}">{window.count:,}</span>', closest),
        _card("Global Defense Readiness", f"{dot}{ready_name}<small>{ready_tone.rstrip(')')}</small>", ready_detail,
              glow=ready_color),
    ]
    return f'<div class="astraea-kpis">{"".join(cards)}</div>'


def render(view: AsteroidView, today: date | None = None, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    st.html(strip_html(view, today or now.date(), now))
