"""Top command bar: ASTRAEA title, operator/time/feed status pill and the manual Sync action."""

from __future__ import annotations

from datetime import UTC, datetime

import streamlit as st

from planetary_defense.application.dashboard_service import DashboardData, LoadResult
from planetary_defense.ui.messages import feed_status
from planetary_defense.ui.theme import PALETTE

SYNC_REPORT = "sync_report"


def render(result: LoadResult, data: DashboardData) -> None:
    history = result.view is not None and result.view.history_available
    status, level = feed_status(result.source, history)
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    title, action = st.columns([5, 1], vertical_alignment="center")
    with title:
        st.html(
            f"""<div class="astraea-header">
              <div class="astraea-title"><span>ASTRAEA</span>: Planetary Defense Command</div>
              <div class="astraea-pill">OPERATOR · {now} ·
                <span class="astraea-dot" style="color:{PALETTE[level]};background:{PALETTE[level]}"></span>{status}
              </div>
            </div>"""
        )
    with action:
        if st.button("Sync / Refresh Feed", key="sync_feed", type="primary", width="stretch",
                     help="Fetch the last 3 days from NASA now (uses NASA_API_KEY), then reload"):
            with st.spinner("Syncing with NASA NeoWs..."):
                st.session_state[SYNC_REPORT] = data.sync()
            st.rerun()

    report = st.session_state.pop(SYNC_REPORT, None)
    if report is not None:
        (st.success if report.ok else st.warning)(report.message)
