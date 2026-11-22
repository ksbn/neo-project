"""Top 4-card KPI strip: Threat Level, NEOs (with a line sparkline), Close Approaches (24h), Defense Readiness.

Drawn in one self-contained iframe because Streamlit's HTML sanitiser drops inline SVG
(the sparkline). Every figure comes from the already-loaded view (stored data), so the
strip renders even when a live sync fails. NASA-derived text is HTML-escaped.
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

HEIGHT: Final = 116
SEGMENTS: Final = ("#27ae60", "#6fcf97", "#f2c94c", "#f2994a", "#eb5757")

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
    "info": ("Standby (Blue)", "#56a8f5"),
}


def threat(view: AsteroidView, today: date) -> tuple[int, str, str]:
    """(level 1-5, label, colour) from the worst alert on passes within +-7 days."""
    level, name = THREAT_SCALE[threat_level(view.alerts, today)]
    return level, f"{name} (Level {level})", SEGMENTS[level - 1]


def readiness(view: AsteroidView, now: datetime) -> tuple[str, str, str]:
    """(label, colour, detail) from the pipeline's health."""
    if not view.history_available:
        return "Degraded (Yellow)", PALETTE["watch"], "Database unavailable · live NASA fallback"
    label, color = READINESS[run_health(view.status, now).level]
    last = view.status.last_success_at if view.status else None
    return label, color, f"Last sync: {last.astimezone(UTC):%Y-%m-%d %H:%M} UTC" if last else "No successful sync yet"


def sparkline_svg(values: list[int], width: int = 220, height: int = 38, color: str = PALETTE["accent"]) -> str:
    """A small line chart with a soft fill; a flat line for fewer than two points."""
    if len(values) < 2:
        y = height / 2
        return (f'<svg viewBox="0 0 {width} {height}" preserveAspectRatio="none"><line x1="0" y1="{y}" '
                f'x2="{width}" y2="{y}" stroke="{color}" stroke-opacity="0.4"/></svg>')
    top, bottom = max(values), min(values)
    span = (top - bottom) or 1
    step = width / (len(values) - 1)
    pts = [(i * step, height - 3 - (v - bottom) / span * (height - 6)) for i, v in enumerate(values)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"0,{height} {line} {width},{height}"
    return (f'<svg viewBox="0 0 {width} {height}" preserveAspectRatio="none" role="img" aria-label="trend">'
            f'<polygon points="{area}" fill="{color}" fill-opacity="0.12"/>'
            f'<polyline points="{line}" fill="none" stroke="{color}" stroke-width="1.8"/></svg>')


def strip_html(view: AsteroidView, today: date, now: datetime) -> str:
    level, threat_label, threat_color = threat(view, today)
    segments = "".join(
        f'<i style="background:{c};opacity:{1 if i < level else 0.25}"></i>' for i, c in enumerate(SEGMENTS)
    )
    window = window_summary(view.rows, today, days=0)
    closest = (f"Closest today: {window.closest_ld:.2f} LD · {html.escape(window.closest.name)}"
               if window.closest is not None and window.closest_ld is not None else "No passes recorded today")
    ready_label, ready_color, ready_detail = readiness(view, now)
    trend = [n for _, n in view.trend]
    p = PALETTE
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
  body {{ margin:0; background:transparent; font:13px/1.35 system-ui,-apple-system,"Segoe UI",sans-serif;
          color:{p["text"]}; }}
  .strip {{ display:grid; grid-template-columns:repeat(4,1fr); gap:14px; }}
  .card {{ background:{p["panel"]}; border:1px solid {p["border"]}; border-radius:8px; padding:10px 14px;
           height:{HEIGHT - 24}px; overflow:hidden; }}
  .t {{ display:flex; justify-content:space-between; font-weight:700; font-size:12.5px; letter-spacing:.03em;
        text-transform:uppercase; }}
  .t .i {{ width:16px; height:16px; border-radius:50%; border:1.5px solid {p["accent"]}; color:{p["accent"]};
           font-size:10px; display:grid; place-items:center; text-transform:none; }}
  .v {{ font-size:22px; margin-top:4px; }}
  .s {{ color:{p["muted"]}; font-size:12px; margin-top:2px; white-space:nowrap; overflow:hidden;
        text-overflow:ellipsis; }}
  .bar {{ display:flex; gap:4px; margin-top:8px; }} .bar i {{ flex:1; height:5px; border-radius:3px; }}
  .neo {{ display:flex; align-items:end; gap:10px; }} .neo svg {{ flex:1; height:38px; }}
  .dot {{ display:inline-block; width:14px; height:14px; border-radius:50%; margin-right:8px; vertical-align:-1px;
          box-shadow:0 0 10px currentColor; }}
</style></head><body><div class="strip">
  <div class="card"><div class="t">Threat Level: <span class="i">i</span></div>
    <div class="v" style="color:{threat_color}">{threat_label}</div><div class="bar">{segments}</div></div>
  <div class="card"><div class="t">Near-Earth Objects (NEOs) <span class="i">i</span></div>
    <div class="neo"><div class="v">Tracked: <span style="color:{p["accent"]}">{view.overview.total:,}</span></div>
    {sparkline_svg(trend)}</div>
    <div class="s">{view.overview.hazardous:,} potentially hazardous · approaches/day, {len(trend)} days
    </div></div>
  <div class="card"><div class="t">Close Approaches (24h): <span class="i">i</span></div>
    <div class="v" style="color:{p["accent"]}">{window.count:,}</div><div class="s">{closest}</div></div>
  <div class="card"><div class="t">Global Defense Readiness: <span class="i">i</span></div>
    <div class="v" style="color:{ready_color}"><span class="dot" style="color:{ready_color};background:{ready_color}">
    </span>{ready_label}</div><div class="s">{ready_detail}</div></div>
</div></body></html>"""


def render(view: AsteroidView, today: date | None = None, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    st.iframe(strip_html(view, today or now.date(), now), height=HEIGHT)
