"""Top command bar: ASTRAEA title, the operator profile pill (time and feed status) and Sync."""

from __future__ import annotations

from datetime import UTC, datetime

import streamlit as st

from planetary_defense.application.dashboard_service import DashboardData, LoadResult
from planetary_defense.ui.messages import feed_status
from planetary_defense.ui.theme import PALETTE

SYNC_REPORT = "sync_report"


def header_html(result: LoadResult, now: datetime) -> str:
    history = result.view is not None and result.view.history_available
    status, level = feed_status(result.source, history)
    color = PALETTE[level]
    return f"""<div class="astraea-header">
      <div class="astraea-title"><b>ASTRAEA:</b> Planetary Defense Command</div>
      <div class="astraea-profile">
        <div class="astraea-avatar">OP</div>
        <div><div class="who">Global Profile</div>
          <div class="when">{now:%d-%b-%Y %H:%M:%S} UTC ·
            <span class="astraea-dot" style="color:{color};background:{color}"></span>{status}</div></div>
        <span class="chev">⌄</span>
      </div>
    </div>"""


def render(result: LoadResult, data: DashboardData) -> None:
    title, action = st.columns([9, 1], vertical_alignment="center")
    with title:
        st.html(header_html(result, datetime.now(UTC)))
    with action:
        if st.button("⟳ Sync", key="sync_feed", width="stretch",
                     help="Sync / refresh feed: fetch the last 3 days from NASA now (uses NASA_API_KEY)"):
            with st.spinner("Syncing with NASA NeoWs..."):
                st.session_state[SYNC_REPORT] = data.sync()
            st.rerun()

    report = st.session_state.pop(SYNC_REPORT, None)
    if report is not None:
        (st.success if report.ok else st.warning)(report.message)
