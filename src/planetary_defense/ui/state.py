"""Session-state helpers: which asteroid the inspector shows.

The alerts panel, the approach table and the inspector's own picker all write
the same key, so selecting a row anywhere drives the inspector.
"""

from __future__ import annotations

from collections.abc import Callable

import streamlit as st

from planetary_defense.domain.models import AsteroidView

SELECTED = "selected_neo_id"


def default_selection(view: AsteroidView) -> str | None:
    """The asteroid of the most urgent alert, else the newest approach."""
    if view.alerts:
        return view.alerts[0].neo_reference_id
    return view.rows[0].neo_reference_id if view.rows else None


def ensure_selection(view: AsteroidView, options: list[str]) -> None:
    """Keep the selection valid: default it on first load, reset it if it's no longer listed."""
    current = st.session_state.get(SELECTED)
    if current not in options:
        fallback = default_selection(view)
        st.session_state[SELECTED] = fallback if fallback in options else (options[0] if options else None)


def on_row_selected(table_key: str, ids: list[str]) -> Callable[[], None]:
    """A dataframe ``on_select`` callback that makes the selected row's asteroid the inspected one."""

    def callback() -> None:
        event = st.session_state.get(table_key)
        rows = event["selection"]["rows"] if event else []
        if rows and rows[0] < len(ids):
            st.session_state[SELECTED] = ids[rows[0]]

    return callback


def select(neo_reference_id: str) -> None:
    """Button callback: inspect this asteroid."""
    st.session_state[SELECTED] = neo_reference_id
