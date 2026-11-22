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
    "bg": "#081219",
    "panel": "#0f1e29",
    "panel_hi": "#132634",
    "border": "#1f3a4a",
    "text": "#dbe7ee",
    "muted": "#8ea3b0",
    "accent": "#3fd0c9",
    "nominal": "#3ddc84",
    "watch": "#f2c94c",
    "warning": "#f2994a",
    "critical": "#eb5757",
}

SEVERITY_COLORS: Final = {
    Severity.INFO: "#8fb8c9",
    Severity.WATCH: PALETTE["watch"],
    Severity.WARNING: PALETTE["warning"],
    Severity.CRITICAL: PALETTE["critical"],
}

MONO: Final = "'JetBrains Mono', 'SFMono-Regular', Menlo, Consolas, monospace"

CSS: Final = f"""
.stApp {{ background: radial-gradient(1200px 600px at 50% -10%, #11283a 0%, {PALETTE["bg"]} 60%); }}
.block-container {{ padding-top: 0.8rem; padding-bottom: 1rem; max-width: 1680px; }}
[data-testid="stHeader"] {{ background: transparent; height: 0; }}
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"] {{ display: none; }}
[data-testid="stVerticalBlockBorderWrapper"] {{ background: {PALETTE["panel"]}; border-radius: 8px;
                                               border-color: {PALETTE["border"]} !important; }}
[data-testid="stDataFrame"] {{ border-radius: 6px; }}
.astraea-panel-title {{ display: flex; justify-content: space-between; align-items: center; font-weight: 700;
                        font-size: 0.86rem; letter-spacing: 0.04em; text-transform: uppercase; color: {PALETTE["text"]};
                        padding-bottom: 6px; border-bottom: 1px solid {PALETTE["border"]}; margin-bottom: 4px; }}
.astraea-panel-title .chev {{ color: {PALETTE["muted"]}; font-weight: 400; }}
.astraea-subtitle {{ display: grid; grid-template-columns: 1fr 70px 96px 70px; gap: 8px; font-size: 0.78rem;
                     font-weight: 700; text-transform: uppercase; color: {PALETTE["text"]}; padding: 2px 10px; }}
.astraea-subtitle span:not(:first-child) {{ text-transform: none; font-weight: 500; color: {PALETTE["muted"]};
                                            text-align: center; }}
.astraea-header {{ display: flex; align-items: center; justify-content: space-between; gap: 16px;
                   padding: 10px 16px; border: 1px solid {PALETTE["border"]}; border-radius: 8px;
                   background: linear-gradient(90deg, #0e2232, {PALETTE["panel"]}); }}
.astraea-title {{ font-size: 1.5rem; color: {PALETTE["text"]}; letter-spacing: 0.01em; }}
.astraea-title b {{ font-weight: 800; }}
.astraea-profile {{ display: flex; align-items: center; gap: 10px; padding: 4px 12px 4px 4px;
                    border: 1px solid {PALETTE["border"]}; border-radius: 8px; background: {PALETTE["panel_hi"]}; }}
.astraea-avatar {{ width: 34px; height: 34px; border-radius: 50%; display: grid; place-items: center;
                   background: linear-gradient(135deg, #2b6777, #173a48); color: {PALETTE["text"]};
                   font-weight: 700; font-size: 0.8rem; }}
.astraea-profile .who {{ font-size: 0.86rem; color: {PALETTE["text"]}; line-height: 1.1; }}
.astraea-profile .when {{ font-family: {MONO}; font-size: 0.7rem; color: {PALETTE["muted"]}; }}
.astraea-profile .chev {{ color: {PALETTE["muted"]}; margin-left: 6px; }}
.astraea-dot {{ display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 6px;
                vertical-align: middle; box-shadow: 0 0 8px currentColor; }}
.astraea-badge {{ display: inline-block; font-family: {MONO}; font-size: 0.68rem; font-weight: 700;
                  letter-spacing: 0.06em; padding: 2px 8px; border-radius: 4px; text-transform: uppercase; }}
.astraea-alert {{ display: grid; grid-template-columns: 26px 1fr 70px 96px 70px; gap: 8px; align-items: center;
                  border-left: 4px solid; border-radius: 4px; background: {PALETTE["panel_hi"]};
                  padding: 7px 10px; margin: 2px 0; }}
.astraea-alert .icon {{ font-size: 1.1rem; text-align: center; }}
.astraea-alert .label {{ font-weight: 700; font-size: 0.86rem; }}
.astraea-alert .why {{ font-size: 0.78rem; color: {PALETTE["text"]}; }}
.astraea-alert .cell {{ font-family: {MONO}; font-size: 0.74rem; color: {PALETTE["muted"]}; text-align: center;
                        line-height: 1.25; }}
.astraea-alert .cell b {{ color: {PALETTE["text"]}; font-weight: 600; display: block; }}
.astraea-telemetry {{ display: grid; grid-template-columns: 150px 1fr 1fr; gap: 10px; align-items: stretch; }}
.astraea-telemetry .name {{ display: grid; place-items: center; border: 1px solid {PALETTE["border"]};
                            border-radius: 6px; background: {PALETTE["panel_hi"]}; font-size: 1.15rem;
                            font-weight: 600; text-align: center; padding: 8px; }}
.astraea-telemetry .rows {{ display: grid; gap: 2px; }}
.astraea-telemetry .row {{ display: flex; justify-content: space-between; gap: 10px; padding: 4px 8px;
                           border-radius: 4px; background: {PALETTE["panel_hi"]}; font-size: 0.84rem; }}
.astraea-telemetry .row span:last-child {{ font-family: {MONO}; color: {PALETTE["text"]}; text-align: right; }}
.astraea-footer {{ display: flex; justify-content: flex-end; gap: 22px; padding: 8px 4px 0;
                   font-size: 0.84rem; color: {PALETTE["text"]}; border-top: 1px solid {PALETTE["border"]};
                   margin-top: 6px; }}
"""


def apply() -> None:
    """Inject the styling layer (a style-only ``st.html`` takes no space on the page)."""
    st.html(f"<style>{CSS}</style>")


def badge(text: str, color: str) -> str:
    """HTML for a severity/status badge. ``text`` must already be escaped."""
    return f'<span class="astraea-badge" style="color:{color};border:1px solid {color}">{text}</span>'
