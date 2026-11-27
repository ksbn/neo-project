"""ASTRAEA styling layer: the "glass over deep space" design system for the Streamlit Control Room.

Design language (Apple-style glass, after star-map apps and NASA's Eyes on the Earth):

* **Space, not a page.** A deep navy-to-black sky with soft nebula glows and two layers of
  deterministic starfield tiles (cool white, ice blue and a few warm gold stars) fixed behind everything.
* **Glass panels.** One recipe everywhere: translucent gradient fill, backdrop blur with saturation,
  a hairline white border, a top inner highlight and a soft drop shadow. Large radii (22px panels,
  16px tiles, full pills and round icon buttons).
* **Type.** The system font (SF Pro on Apple devices), light large numerals with tabular figures,
  small uppercase labels with letter-spacing. Labels on the sky are white; planets are gold.
* **Colour carries meaning only.** Ice blue for interactive and data accents, gold for highlights,
  mint for nominal, and softened, glowing severity colours (watch, warning, critical).

The base theme (dark colours, font, radii, data table header) is in ``.streamlit/config.toml``; this
module adds the CSS on top of Streamlit's own widgets, so no native behaviour is replaced.
"""

from __future__ import annotations

import random
from typing import Final
from urllib.parse import quote

import streamlit as st

from planetary_defense.domain.models import Severity

PALETTE: Final = {
    "bg": "#050912",
    "panel": "rgba(26, 36, 66, 0.42)",        # glass fill (over the sky)
    "panel_hi": "rgba(255, 255, 255, 0.06)",  # glass tile inside a panel
    "border": "rgba(255, 255, 255, 0.12)",    # hairline
    "text": "#eef3ff",
    "muted": "rgba(226, 234, 255, 0.58)",
    "accent": "#8ec5ff",  # ice blue: star labels, data, interactive
    "gold": "#f3c46b",    # planet labels, highlights
    "nominal": "#5ee6b5",
    "watch": "#ffd66b",
    "warning": "#ffad5c",
    "critical": "#ff6b7a",
}

SEVERITY_COLORS: Final = {
    Severity.INFO: PALETTE["accent"],
    Severity.WATCH: PALETTE["watch"],
    Severity.WARNING: PALETTE["warning"],
    Severity.CRITICAL: PALETTE["critical"],
}

MONO: Final = '"SF Mono", ui-monospace, Menlo, Consolas, monospace'


def starfield(size: int, count: int, seed: int) -> str:
    """A tileable SVG starfield as a CSS ``url()``; deterministic, so the sky never changes between reruns."""
    rng = random.Random(seed)
    colours = ["#ffffff"] * 6 + ["#cfe3ff"] * 3 + ["#ffe2a8"]  # mostly white, some ice blue, a few warm stars
    stars = "".join(
        f'<circle cx="{rng.uniform(0, size):.1f}" cy="{rng.uniform(0, size):.1f}" '
        f'r="{rng.choice((0.5, 0.6, 0.7, 0.8, 1.0, 1.3)):.1f}" fill="{rng.choice(colours)}" '
        f'opacity="{rng.uniform(0.25, 0.9):.2f}"/>'
        for _ in range(count)
    )
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}">{stars}</svg>'
    return f'url("data:image/svg+xml,{quote(svg)}")'


SKY: Final = ", ".join((
    "radial-gradient(900px 620px at 12% 8%, rgba(79, 112, 255, 0.20), transparent 62%)",
    "radial-gradient(760px 520px at 88% 18%, rgba(142, 197, 255, 0.13), transparent 60%)",
    "radial-gradient(1100px 760px at 55% 115%, rgba(139, 92, 246, 0.16), transparent 62%)",
    starfield(520, 110, seed=7),
    starfield(1160, 70, seed=19),
    "linear-gradient(180deg, #050912 0%, #0a1430 55%, #060a17 100%)",
))

#: The one glass recipe for panels; rows and tiles inside panels use a lighter, unblurred fill.
GLASS: Final = f"""
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.085), rgba(255, 255, 255, 0.025)), {PALETTE["panel"]};
  -webkit-backdrop-filter: blur(24px) saturate(170%);
  backdrop-filter: blur(24px) saturate(170%);
  border: 1px solid {PALETTE["border"]} !important;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.16), 0 18px 50px rgba(0, 0, 0, 0.38);
"""

CSS: Final = f"""
/* ---------------------------------------------------------------- sky and page */
.stApp {{ background: {SKY}; background-attachment: fixed; color: {PALETTE["text"]}; }}
.block-container {{ padding-top: 1.1rem; padding-bottom: 2rem; max-width: 1680px; }}
[data-testid="stHeader"] {{ background: transparent; height: 0; }}
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"] {{ display: none; }}
.stApp {{ -webkit-font-smoothing: antialiased; }}

/* ---------------------------------------------------------------- glass panels
   Every panel is st.container(border=True, key="panel_..."); Streamlit gives a keyed container the class
   "st-key-" + key, a stable hook (the DOM around it changes between Streamlit releases). */
[class*="st-key-panel_"] {{ {GLASS} border-radius: 22px !important; padding: 18px 18px 14px !important; }}
[data-testid="stExpander"] details {{ {GLASS} border-radius: 18px !important; }}
[data-testid="stExpander"] summary:hover {{ color: {PALETTE["accent"]}; }}
[data-testid="stDataFrame"] {{ border-radius: 14px; overflow: hidden; }}
[data-testid="stAlert"] {{ border-radius: 16px; -webkit-backdrop-filter: blur(18px); backdrop-filter: blur(18px);
                          border: 1px solid {PALETTE["border"]}; }}

/* ---------------------------------------------------------------- controls: pills and round icon buttons */
.stButton button {{
  border-radius: 999px; background: rgba(255, 255, 255, 0.07); color: {PALETTE["text"]};
  border: 1px solid {PALETTE["border"]}; -webkit-backdrop-filter: blur(16px); backdrop-filter: blur(16px);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.14); transition: background .2s, border-color .2s, transform .1s; }}
.stButton button:hover {{ background: rgba(142, 197, 255, 0.16); border-color: rgba(142, 197, 255, 0.55);
                          color: #ffffff; }}
.stButton button:active {{ transform: scale(0.97); }}
.stButton button:focus-visible {{ outline: 2px solid {PALETTE["accent"]}; outline-offset: 2px; }}
/* the track buttons (crosshair icon) are round glass icon buttons, like a star map's toolbar */
[class*="st-key-track_"] button, [class*="st-key-upcoming_"] button {{
  width: 38px; height: 38px; min-height: 38px; padding: 0; border-radius: 50%; }}
[class*="st-key-track_"] button span, [class*="st-key-upcoming_"] button span {{ color: {PALETTE["accent"]}; }}
.st-key-sync_feed button {{ height: 48px; font-weight: 600; letter-spacing: 0.02em;
  background: linear-gradient(180deg, rgba(142, 197, 255, 0.24), rgba(142, 197, 255, 0.08));
  border-color: rgba(142, 197, 255, 0.45); }}
[data-baseweb="select"] > div, [data-testid="stTextInput"] input {{
  background: rgba(255, 255, 255, 0.06) !important; border: 1px solid {PALETTE["border"]} !important;
  border-radius: 999px !important; -webkit-backdrop-filter: blur(16px); backdrop-filter: blur(16px); }}
[data-testid="stTextInput"] input {{ padding-left: 16px; }}

/* ---------------------------------------------------------------- titles */
.astraea-panel-title {{ display: flex; justify-content: space-between; align-items: center; font-weight: 600;
  font-size: 0.74rem; letter-spacing: 0.14em; text-transform: uppercase; color: {PALETTE["muted"]};
  padding: 2px 4px 10px; margin-bottom: 4px; border-bottom: 1px solid rgba(255, 255, 255, 0.07); }}
.astraea-panel-title .chev {{ display: none; }}
.astraea-subtitle {{ display: grid; grid-template-columns: 1fr 70px 96px 70px; gap: 8px; font-size: 0.72rem;
  font-weight: 600; letter-spacing: 0.1em; text-transform: uppercase; color: {PALETTE["muted"]}; padding: 2px 12px; }}
.astraea-subtitle span {{ white-space: nowrap; }}
.astraea-subtitle span:not(:first-child) {{ letter-spacing: 0.04em; text-align: center; font-size: 0.66rem; }}

/* ---------------------------------------------------------------- command header */
.astraea-header {{ {GLASS} display: flex; align-items: center; justify-content: space-between; gap: 16px;
  padding: 12px 14px 12px 22px; border-radius: 22px; }}
.astraea-title {{ font-size: 1.45rem; color: {PALETTE["text"]}; font-weight: 300; letter-spacing: 0.01em; }}
.astraea-title b {{ font-weight: 700; letter-spacing: 0.1em; margin-right: 8px; }}
.astraea-title .sub {{ color: {PALETTE["muted"]}; }}
.astraea-profile {{ display: flex; align-items: center; gap: 10px; padding: 5px 16px 5px 5px; border-radius: 999px;
  background: rgba(255, 255, 255, 0.06); border: 1px solid {PALETTE["border"]}; }}
.astraea-avatar {{ width: 34px; height: 34px; border-radius: 50%; display: grid; place-items: center;
  background: radial-gradient(circle at 35% 30%, #cfe3ff 0%, #5b7fd6 45%, #1b2a5a 100%); color: #061022;
  font-weight: 700; font-size: 0.72rem; box-shadow: 0 0 18px rgba(142, 197, 255, 0.45); }}
.astraea-profile .who {{ font-size: 0.84rem; color: {PALETTE["text"]}; line-height: 1.15; font-weight: 500; }}
.astraea-profile .when {{ font-size: 0.72rem; color: {PALETTE["muted"]}; font-variant-numeric: tabular-nums; }}
.astraea-profile .chev {{ display: none; }}
.astraea-dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px;
  vertical-align: middle; box-shadow: 0 0 10px currentColor, 0 0 2px currentColor; }}
.astraea-badge {{ display: inline-block; font-size: 0.66rem; font-weight: 700; letter-spacing: 0.08em;
  padding: 3px 9px; border-radius: 999px; text-transform: uppercase; }}

/* ---------------------------------------------------------------- KPI cards */
.astraea-kpis {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin: 4px 0 6px; }}
@media (max-width: 1100px) {{ .astraea-kpis {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
.astraea-kpi {{ {GLASS} border-radius: 22px; padding: 16px 20px; min-height: 124px; position: relative;
  overflow: hidden; }}
.astraea-kpi::after {{ content: ""; position: absolute; right: -70px; bottom: -110px; width: 230px; height: 230px;
  border-radius: 50%; background: radial-gradient(circle, var(--glow, rgba(142, 197, 255, 0.16)), transparent 70%);
  pointer-events: none; }}
.astraea-kpi .label {{ font-size: 0.7rem; font-weight: 600; letter-spacing: 0.14em; text-transform: uppercase;
  color: {PALETTE["muted"]}; }}
.astraea-kpi .value {{ font-size: 2rem; font-weight: 300; line-height: 1.15; margin-top: 8px;
  font-variant-numeric: tabular-nums; letter-spacing: -0.01em; }}
.astraea-kpi .value small {{ font-size: 0.95rem; color: {PALETTE["muted"]}; font-weight: 400; margin-left: 4px; }}
.astraea-kpi .sub {{ font-size: 0.78rem; color: {PALETTE["muted"]}; margin-top: 6px; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; }}
.astraea-scale {{ display: flex; gap: 5px; margin-top: 12px; }}
.astraea-scale i {{ flex: 1; height: 6px; border-radius: 999px; }}
.astraea-bars {{ display: flex; align-items: flex-end; gap: 3px; height: 34px; margin-top: 10px; }}
.astraea-bars i {{ flex: 1; border-radius: 3px 3px 1px 1px; min-height: 2px;
  background: linear-gradient(180deg, {PALETTE["accent"]}, rgba(142, 197, 255, 0.25)); }}

/* ---------------------------------------------------------------- alert and pass rows */
.astraea-alert {{ display: grid; grid-template-columns: 26px 1fr 70px 96px 70px; gap: 8px; align-items: center;
  border-radius: 16px; padding: 9px 12px; margin: 3px 0; background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.08); border-left: 3px solid; transition: background .2s; }}
.astraea-alert:hover {{ background: rgba(255, 255, 255, 0.09); }}
.astraea-alert.upcoming {{ grid-template-columns: 26px 1fr 92px 84px 70px; }}
.astraea-alert .icon {{ font-size: 1rem; text-align: center; filter: drop-shadow(0 0 6px currentColor); }}
.astraea-alert .label {{ font-weight: 600; font-size: 0.86rem; }}
.astraea-alert .why {{ font-size: 0.76rem; color: {PALETTE["muted"]}; }}
.astraea-alert .cell {{ font-size: 0.68rem; color: {PALETTE["muted"]}; text-align: center; line-height: 1.3;
  letter-spacing: 0.04em; }}
.astraea-alert .cell b {{ color: {PALETTE["text"]}; font-weight: 500; display: block; font-size: 0.8rem;
  font-variant-numeric: tabular-nums; letter-spacing: 0; }}

/* ---------------------------------------------------------------- telemetry */
.astraea-telemetry {{ display: grid; grid-template-columns: 160px 1fr 1fr; gap: 10px; align-items: stretch; }}
.astraea-telemetry .name {{ display: grid; place-items: center; border-radius: 18px; text-align: center;
  padding: 12px; font-size: 1.05rem; font-weight: 600; color: {PALETTE["gold"]};
  background: radial-gradient(circle at 50% 0%, rgba(243, 196, 107, 0.18), transparent 70%), rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.08); text-shadow: 0 0 18px rgba(243, 196, 107, 0.35); }}
.astraea-telemetry .rows {{ display: grid; gap: 4px; }}
.astraea-telemetry .row {{ display: flex; justify-content: space-between; gap: 10px; padding: 6px 12px;
  border-radius: 12px; background: rgba(255, 255, 255, 0.045); font-size: 0.82rem; color: {PALETTE["muted"]}; }}
.astraea-telemetry .row span:last-child {{ color: {PALETTE["text"]}; text-align: right; font-weight: 500;
  font-variant-numeric: tabular-nums; }}
.astraea-footer {{ display: flex; justify-content: flex-end; flex-wrap: wrap; gap: 8px; padding: 4px 2px 0;
  font-size: 0.78rem; color: {PALETTE["text"]}; }}
.astraea-footer > span {{ padding: 5px 12px; border-radius: 999px; background: rgba(255, 255, 255, 0.06);
  border: 1px solid {PALETTE["border"]}; -webkit-backdrop-filter: blur(16px); backdrop-filter: blur(16px); }}

@media (prefers-reduced-motion: reduce) {{ .stApp * {{ transition: none !important; }} }}
"""


def apply() -> None:
    """Inject the styling layer (a style-only ``st.html`` takes no space on the page).

    The CSS must never contain ``<`` or ``>``: Streamlit's HTML sanitiser would read them as tags and drop
    the whole style block (``tests/ui/test_theme.py`` guards this).
    """
    st.html(f"<style>{CSS}</style>")


def badge(text: str, color: str) -> str:
    """HTML for a severity/status badge. ``text`` must already be escaped."""
    return (f'<span class="astraea-badge" style="color:{color};border:1px solid {color};'
            f'box-shadow:0 0 12px {color}33">{text}</span>')
