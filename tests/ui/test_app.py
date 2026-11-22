"""The Control Room page (ui/app.py render + components), driven by Streamlit's AppTest with a fake dashboard."""

from streamlit.testing.v1 import AppTest

from planetary_defense.domain.models import Severity


def page(live: bool = False, empty: bool = False, orbit_fails: bool = False) -> None:
    from planetary_defense.ui.app import render

    from fakes import FakeDashboard  # imported inside: AppTest runs this function's source as the script

    render(FakeDashboard(live=live, empty=empty, orbit_fails=orbit_fails))


def run(**kwargs: bool) -> AppTest:
    at = AppTest.from_function(page, kwargs=kwargs, default_timeout=30).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def metrics(at: AppTest) -> dict[str, str]:
    return {m.label: m.value for m in at.metric}


def test_kpi_bar_shows_whole_dataset_figures():
    shown = metrics(run())
    assert shown["Tracked NEOs"] == "3"
    assert shown["Potentially Hazardous"] == "2"
    assert shown["Closest Pass"] == "15.65 LD"
    assert shown["Fastest Pass"] == "36.9 km/s"
    assert shown["Warnings & Critical"] == "1"


def test_status_banner_and_alerts_panel():
    at = run()
    assert any("Last pipeline run" in s.value for s in [*at.success, *at.warning])
    alerts = at.dataframe[0].value  # watch-or-worse by default: the info alert is hidden
    assert list(alerts["severity"]) == ["Warning", "Watch"]

    at.selectbox(key="alert_floor").set_value(Severity.INFO).run()
    assert len(at.dataframe[0].value) == 3
    at.selectbox(key="alert_floor").set_value(Severity.CRITICAL).run()
    assert any("No critical-or-worse alerts" in c.value for c in at.caption)


def test_inspector_opens_on_the_most_urgent_alert():
    at = run()
    assert at.selectbox(key="inspect_neo").value == "2524522"
    assert any("Potentially hazardous asteroid" in m.value for m in at.markdown)
    assert any("local cache" in c.value for c in at.caption)
    orbit = at.dataframe[2].value  # alerts, approaches, orbit
    assert dict(zip(orbit["Element"], orbit["Value"], strict=True))["Orbit class"] == "ATE"
    assert any("2 recorded approaches" in c.value for c in at.caption)


def test_picking_another_asteroid_updates_the_inspector():
    at = run()
    at.selectbox(key="inspect_neo").set_value("b").run()
    assert not at.exception
    assert metrics(at)["Velocity"] == "36.9 km/s"
    assert any("Relative velocity 36.9 km/s" in m.value for m in at.markdown)


def test_orbit_failure_is_explained_not_fatal():
    at = run(orbit_fails=True)
    assert any("Orbit unavailable" in i.value and "none is cached" in i.value for i in at.info)
    assert any("recorded approaches" in c.value for c in at.caption)  # the rest of the inspector still renders


def test_live_fallback_mode_says_so_and_hides_history():
    at = run(live=True)
    assert any("Showing live NASA data" in i.value for i in at.info)
    assert any("only available from the database" in c.value for c in at.caption)


def test_no_data_at_all_shows_a_warning_and_diagnostics():
    at = run(empty=True)
    assert any("Unable to fetch asteroid data" in w.value for w in at.warning)
    assert [c.value for c in at.sidebar.caption] == ["PostgreSQL database: unavailable", "NASA: unavailable"]


def viewport_payloads(at: AppTest) -> list[dict]:
    import json
    import re

    docs = [frame.proto.srcdoc for frame in at.get("iframe")]
    found = [re.search(r'<script type="application/json" id="payload">(.*?)</script>', d, re.S) for d in docs]
    return [json.loads(m.group(1)) for m in found if m]


def test_inspector_shows_the_3d_view_of_the_selected_asteroid():
    at = run()
    (payload,) = viewport_payloads(at)
    assert payload["name"] == "524522 Zoozve (2002 VE68)" and payload["hazardous"] is True
    assert payload["default_mode"] == "earth" and payload["earth"]["approach_date"] == "2026-11-02"
    assert "local cache" in payload["provenance"]

    at.selectbox(key="inspect_neo").set_value("b").run()
    (payload,) = viewport_payloads(at)
    assert payload["name"] == "(2019 BG3)" and payload["hazardous"] is False


def test_no_3d_view_without_an_orbit():
    assert viewport_payloads(run(orbit_fails=True)) == []


def test_command_header_and_non_blocking_sync_failure():
    at = run()
    assert any("ASTRAEA" in h.proto.body for h in at.get("html"))
    at.button(key="sync_feed").click().run()
    assert not at.exception
    assert any("did not complete" in w.value for w in at.warning)
    assert metrics(at)  # the stored data is still on the page
