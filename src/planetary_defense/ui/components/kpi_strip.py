"""Top 4-card KPI strip: Threat Level, NEOs (with trend), Close Approaches (window), Defense Readiness.

Every figure comes from the already-loaded view (stored data), so the strip renders
even when a live sync fails.
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

THREAT_NAMES: Final = {
    None: ("LOW", PALETTE["nominal"]),
    Severity.INFO: ("LOW", PALETTE["nominal"]),
    Severity.WATCH: ("GUARDED", PALETTE["watch"]),
    Severity.WARNING: ("ELEVATED", PALETTE["warning"]),
    Severity.CRITICAL: ("CRITICAL", PALETTE["critical"]),
}

READINESS: Final = {
    "success": ("NOMINAL", PALETTE["nominal"]),
    "warning": ("DEGRADED", PALETTE["watch"]),
    "error": ("IMPAIRED", PALETTE["critical"]),
    "info": ("STANDBY", PALETTE["accent"]),
}


def sparkline_bars(values: list[int], color: str = PALETTE["accent"]) -> str:
    """A tiny bar sparkline built from styled ``<i>`` elements.

    Streamlit sanitises ``st.html`` (inline ``<svg>`` and data-URI images are dropped), but
    inline styles survive, so bars are plain elements with percentage heights.
    """
    if not values:
        return '<div class="astraea-spark"></div>'
    top = max(values) or 1
    bars = "".join(
        f'<i title="{v}" style="height:{max(8, round(v / top * 100))}%;background:{color}"></i>' for v in values
    )
    return f'<div class="astraea-spark">{bars}</div>'


def _card(label: str, value: str, color: str, sub: str, extra: str = "") -> str:
    return (f'<div class="astraea-card"><div class="label">{label}</div>'
            f'<div class="value" style="color:{color}">{value}</div>{extra}<div class="sub">{sub}</div></div>')


def render(view: AsteroidView, today: date | None = None, now: datetime | None = None) -> None:
    today = today or date.today()
    now = now or datetime.now(UTC)
    threat, threat_color, near = _threat(view, today)
    window = window_summary(view.rows, today)
    readiness, readiness_color, synced = _readiness(view, now)

    closest = (f"closest {window.closest_ld:.2f} LD · {html.escape(window.closest.name)}"
               if window.closest is not None and window.closest_ld is not None else "none in the window")
    trend = [n for _, n in view.trend]
    cards = [
        _card("Threat Level",
              f'<span class="astraea-dot" style="color:{threat_color};background:{threat_color}"></span>{threat}',
              threat_color, near),
        _card("Near-Earth Objects", f"{view.overview.total:,}", PALETTE["text"],
              f"{view.overview.hazardous:,} hazardous · approaches/day, last {len(trend)} days",
              sparkline_bars(trend)),
        _card("Close Approaches (±3 days)", f"{window.count:,}", PALETTE["text"],
              f"{window.start:%b %d}–{window.end:%b %d} · {closest}"),
        _card("Global Defense Readiness",
              f'<span class="astraea-dot" style="color:{readiness_color};background:{readiness_color}"></span>'
              f"{readiness}", readiness_color, synced),
    ]
    for column, card in zip(st.columns(4), cards, strict=True):
        with column:
            st.html(card)


def _threat(view: AsteroidView, today: date) -> tuple[str, str, str]:
    level = threat_level(view.alerts, today)
    name, color = THREAT_NAMES[level]
    near = [a for a in view.alerts if a.close_approach_date and abs((a.close_approach_date - today).days) <= 7]
    if level is None:
        return name, color, "No alerts on passes within ±7 days"
    top = max(near, key=lambda a: a.severity)
    sub = f"{len(near)} alert{'s' if len(near) != 1 else ''} within ±7 days · top: {html.escape(top.name)}"
    return name, color, sub


def _readiness(view: AsteroidView, now: datetime) -> tuple[str, str, str]:
    if not view.history_available:
        return "DEGRADED", PALETTE["watch"], "Database unavailable · showing live NASA fallback"
    health = run_health(view.status, now)
    name, color = READINESS[health.level]
    last = view.status.last_success_at if view.status else None
    synced = f"Last sync {last.astimezone(UTC):%Y-%m-%d %H:%M} UTC" if last else "No successful sync recorded"
    return name, color, synced
