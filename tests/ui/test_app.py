"""The Control Room page (ui/app.py render + components), driven by Streamlit's AppTest with a fake dashboard."""

import pytest
from streamlit.testing.v1 import AppTest

from planetary_defense.domain.models import Severity
from planetary_defense.domain.units import LUNAR_DISTANCE_KM


def page(live: bool = False, empty: bool = False, orbit_fails: bool = False) -> None:
    from datetime import date

    from planetary_defense.ui.app import render

    from fakes import FakeDashboard  # imported inside: AppTest runs this function's source as the script

    # The day of the fake's data, so the date windows (threat level, upcoming horizon) don't drift with the clock.
    render(FakeDashboard(live=live, empty=empty, orbit_fails=orbit_fails), today=date(2026, 11, 22))


def run(**kwargs: bool) -> AppTest:
    at = AppTest.from_function(page, kwargs=kwargs, default_timeout=30).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def metrics(at: AppTest) -> dict[str, str]:
    return {m.label: m.value for m in at.metric}


def page_html(at: AppTest) -> str:
    """Everything rendered as HTML: st.html blocks and iframe documents (KPI strip, 3D viewport)."""
    blocks = [h.proto.body for h in at.get("html")] + [f.proto.srcdoc for f in at.get("iframe")]
    return "\n".join(blocks)


def test_kpi_strip_has_the_four_cards_from_stored_data():
    text = page_html(run())
    for title in ("Threat Level:", "Near-Earth Objects (NEOs)", "Close Approaches (24h):", "Global Defense Readiness:"):
        assert title in text
    assert ">3<" in text  # tracked NEOs
    assert "<polyline" in text  # the approaches-per-day sparkline
    assert "Guarded (Level 3)" in text  # worst alert within +-7 days of the fake's dates is a watch
    assert "Operational (Green)" in text and "Last sync: 2026-11-22 08:00 UTC" in text


def test_healthy_runs_need_no_banner_and_hazard_alerts_filter():
    at = run()
    assert not at.main.success  # the readiness card shows the healthy state instead
    text = page_html(at)
    assert "WARNING" in text and "WATCH" in text and "Classified by NASA" not in text  # info hidden by default
    assert "<b>11-02</b>UTC" in text and "≤450 m" in text  # pass time and estimated size on the row

    at.selectbox(key="alert_floor").set_value(Severity.INFO).run()
    assert "Classified by NASA" in page_html(at)
    at.selectbox(key="alert_floor").set_value(Severity.CRITICAL).run()
    assert any("No critical-or-worse alerts" in c.value for c in at.caption)


def test_close_approach_table_has_the_spec_columns_in_display_units():
    at = run()
    table = at.dataframe[0].value
    assert list(table["Object Name"]) == ["● 524522 Zoozve (2002 VE68)", "● (2019 BG3)", "● (2001 KF54)"]
    assert table["Distance (LD)"].iloc[0] == pytest.approx(6_019_000 / LUNAR_DISTANCE_KM)
    assert table["Diameter (m)"].iloc[0] == pytest.approx(450.0)


def test_stage_opens_on_the_most_urgent_alert_with_telemetry():
    at = run()
    assert at.selectbox(key="selected_neo_id").value == "2524522"
    text = page_html(at)
    assert "Inclination (deg)" in text and "9.037°" in text  # from the cached orbit
    assert "15.65 LD · 6,019,000 km" in text  # minimum Earth distance
    assert "Live Feed" in text and "Data Synced 08:00 UTC" in text and "Orbit: cache" in text


def test_track_button_and_picker_change_the_tracked_asteroid():
    at = run()
    track_b = next(b for b in at.button if b.key and b.key.endswith("_b_high_velocity"))
    track_b.click().run()
    assert at.selectbox(key="selected_neo_id").value == "b"
    (payload,) = viewport_payloads(at)
    assert payload["name"] == "(2019 BG3)" and payload["hazardous"] is False

    at.selectbox(key="selected_neo_id").set_value("a").run()
    assert "<span>Object ID</span><span>a</span>" in page_html(at)


def test_orbit_failure_falls_back_to_an_approximate_pass():
    at = run(orbit_fails=True)
    assert any("Orbit unavailable" in w.value and "approximate" in w.value for w in at.warning)
    (payload,) = viewport_payloads(at)
    assert payload["approximate"] is True and payload["sun"] is None
    assert "Orbit unavailable" in page_html(at)  # the telemetry status shows the error state


def test_live_fallback_mode_says_so_and_hides_history():
    at = run(live=True)
    assert any("Showing live NASA data" in i.value for i in at.info)
    assert any("only available from the database" in c.value for c in at.caption)
    assert "NASA Fallback" in page_html(at)


def test_no_data_at_all_shows_a_warning_and_diagnostics():
    at = run(empty=True)
    assert any("Unable to fetch asteroid data" in w.value for w in at.warning)
    assert [c.value for c in at.main.caption][-2:] == ["PostgreSQL database: unavailable", "NASA: unavailable"]


def viewport_payloads(at: AppTest) -> list[dict]:
    import json
    import re

    docs = [frame.proto.srcdoc for frame in at.get("iframe")]
    found = [re.search(r'<script type="application/json" id="payload">(.*?)</script>', d, re.S) for d in docs]
    return [json.loads(m.group(1)) for m in found if m]


def test_viewport_shows_the_tracked_asteroid():
    (payload,) = viewport_payloads(run())
    assert payload["name"] == "524522 Zoozve (2002 VE68)" and payload["hazardous"] is True
    assert payload["default_mode"] == "earth" and payload["earth"]["approach_date"] == "2026-11-02"
    assert "local cache" in payload["provenance"] and payload["approximate"] is False


def test_command_header_and_non_blocking_sync_failure():
    at = run()
    assert any("ASTRAEA" in h.proto.body for h in at.get("html"))
    at.button(key="sync_feed").click().run()
    assert not at.exception
    assert any("did not complete" in w.value for w in at.warning)
    assert "Threat Level" in page_html(at)  # the stored data is still on the page


def upcoming_rows(at: AppTest) -> list[str]:
    return [h.proto.body for h in at.get("html") if "astraea-alert upcoming" in h.proto.body]


def test_upcoming_panel_lists_predicted_passes_with_alerts_ahead_of_time():
    at = run()
    assert "Upcoming Approaches" in page_html(at)
    first, second = upcoming_rows(at)  # Zoozve's 2034 pass is beyond the default one-year horizon
    # 'a' passes within 4 LD on 2027-03-15: flagged months ahead; 'b' at 30 LD is a nominal pass.
    assert "WARNING: (2001 KF54)" in first and "<b>2027-03-15</b>" in first and "in 4 mo" in first
    assert "(2019 BG3)" in second and "Nominal pass" in second

    at.selectbox(key="upcoming_horizon").set_value("Next 10 years").run()
    assert "<b>2034-10-31</b>" in upcoming_rows(at)[-1]  # Zoozve's next pass

    at.checkbox(key="upcoming_alerted_only").check().run()
    assert len(upcoming_rows(at)) == 2 and not any("Nominal pass" in r for r in upcoming_rows(at))


def test_upcoming_track_button_tracks_the_asteroid():
    at = run()
    next(b for b in at.button if b.key and b.key.startswith("upcoming_") and b.key.endswith("_a")).click().run()
    assert at.selectbox(key="selected_neo_id").value == "a"


def test_telemetry_shows_the_next_predicted_pass():
    text = page_html(run())
    assert "Next Earth Pass (predicted)" in text and "2034-10-31 · 16.80 LD" in text  # Zoozve, from its orbit
    assert "<span>Orbit Class</span><span>ATE</span>" in text


def test_live_fallback_has_no_predictions():
    at = run(live=True)
    assert any("orbit cache, which is unavailable" in c.value for c in at.caption)
