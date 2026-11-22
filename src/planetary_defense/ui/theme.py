"""ASTRAEA styling layer: palette, severity colours and the CSS that restyles Streamlit.

The base theme (dark, colours) is in ``.streamlit/config.toml``; this module only
adds control-room details (panels, telemetry typography, badges, compact spacing)
on top of Streamlit's own widgets, so no native behaviour is replaced.
"""

from __future__ import annotations

from typing import Final

import streamlit as st

from planetary_defense.domain.models import Severity

PALETTE: Final = {
    "bg": "#070b14",
    "panel": "#0f1626",
    "border": "#1f2a40",
    "text": "#d7dde8",
    "muted": "#8b95a7",
    "accent": "#4da3ff",
    "nominal": "#3ddc97",
    "watch": "#f5c542",
    "warning": "#ff9f43",
    "critical": "#ff4d4f",
}

SEVERITY_COLORS: Final = {
    Severity.INFO: PALETTE["accent"],
    Severity.WATCH: PALETTE["watch"],
    Severity.WARNING: PALETTE["warning"],
    Severity.CRITICAL: PALETTE["critical"],
}

MONO: Final = "'JetBrains Mono', 'SFMono-Regular', Menlo, Consolas, monospace"

CSS: Final = f"""
.block-container {{ padding-top: 1.2rem; padding-bottom: 1.5rem; max-width: 1600px; }}
[data-testid="stHeader"] {{ background: transparent; }}
h1, h2, h3 {{ letter-spacing: 0.02em; }}
h3 {{ font-size: 0.95rem !important; text-transform: uppercase; color: {PALETTE["muted"]} !important;
      font-family: {MONO}; font-weight: 600 !important; }}
[data-testid="stDataFrame"] {{ border: 1px solid {PALETTE["border"]}; border-radius: 6px; }}
.astraea-header {{ display: flex; align-items: center; justify-content: space-between; gap: 16px;
                   padding: 10px 16px; border: 1px solid {PALETTE["border"]}; border-radius: 8px;
                   background: linear-gradient(90deg, #0d1a33, {PALETTE["panel"]}); }}
.astraea-title {{ font-family: {MONO}; font-size: 1.15rem; font-weight: 700; letter-spacing: 0.06em;
                  color: {PALETTE["text"]}; }}
.astraea-title span {{ color: {PALETTE["accent"]}; }}
.astraea-pill {{ font-family: {MONO}; font-size: 0.75rem; color: {PALETTE["muted"]};
                 border: 1px solid {PALETTE["border"]}; border-radius: 999px; padding: 4px 12px;
                 white-space: nowrap; }}
.astraea-dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px;
                vertical-align: middle; box-shadow: 0 0 6px currentColor; }}
.astraea-card {{ border: 1px solid {PALETTE["border"]}; border-radius: 8px; background: {PALETTE["panel"]};
                 padding: 12px 14px; height: 100%; min-height: 138px; box-sizing: border-box; }}
.astraea-card .label {{ font-family: {MONO}; font-size: 0.7rem; text-transform: uppercase;
                        letter-spacing: 0.08em; color: {PALETTE["muted"]}; }}
.astraea-card .value {{ font-family: {MONO}; font-size: 1.6rem; font-weight: 700; margin: 4px 0 2px; }}
.astraea-card .sub {{ font-size: 0.78rem; color: {PALETTE["muted"]}; }}
.astraea-badge {{ display: inline-block; font-family: {MONO}; font-size: 0.68rem; font-weight: 700;
                  letter-spacing: 0.06em; padding: 2px 8px; border-radius: 4px; text-transform: uppercase; }}
.astraea-alert {{ border-left: 3px solid; border-radius: 4px; background: {PALETTE["panel"]};
                  padding: 8px 10px; margin-bottom: 2px; }}
.astraea-alert .name {{ font-weight: 600; }}
.astraea-alert .meta {{ font-family: {MONO}; font-size: 0.72rem; color: {PALETTE["muted"]}; }}
.astraea-spark {{ display: flex; align-items: flex-end; gap: 2px; height: 26px; margin: 2px 0 4px; }}
.astraea-spark i {{ flex: 1; max-width: 8px; border-radius: 1px; opacity: 0.85; }}
.astraea-readouts {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px; }}
.astraea-readout {{ border: 1px solid {PALETTE["border"]}; border-radius: 6px; padding: 8px 10px;
                    background: {PALETTE["panel"]}; }}
.astraea-readout .k {{ font-family: {MONO}; font-size: 0.66rem; text-transform: uppercase;
                       letter-spacing: 0.08em; color: {PALETTE["muted"]}; }}
.astraea-readout .v {{ font-family: {MONO}; font-size: 0.95rem; font-weight: 600; margin-top: 2px; }}
.astraea-status {{ font-family: {MONO}; font-size: 0.72rem; color: {PALETTE["muted"]}; margin-top: 8px; }}
.astraea-status b {{ color: {PALETTE["text"]}; font-weight: 600; margin-right: 14px; }}
"""


def apply() -> None:
    """Inject the styling layer (a style-only ``st.html`` takes no space on the page)."""
    st.html(f"<style>{CSS}</style>")


def badge(text: str, color: str) -> str:
    """HTML for a severity/status badge. ``text`` must already be escaped."""
    return f'<span class="astraea-badge" style="color:{color};border:1px solid {color}">{text}</span>'
