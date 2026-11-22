"""The dashboard (web/static) is served by the API and uses only the contract: no side doors."""

from __future__ import annotations

import re
from pathlib import Path

from planetary_defense.api.app import STATIC

from contract import operations

SOURCES = {p.name: p.read_text() for p in STATIC.glob("*.js")}


def test_the_dashboard_and_its_modules_are_served(api):
    page = api.client.get("/")
    assert page.status_code == 200 and '<script type="module" src="app.js">' in page.text
    imported = {m for src in SOURCES.values() for m in re.findall(r'from "\./([\w-]+\.js)"', src)}
    for name in imported | {"app.js", "app.css"}:
        assert api.client.get(f"/{name}").status_code == 200, name


def test_the_ui_calls_only_operations_in_the_contract():
    """Every request() path in api.js, as a template, is an operation of openapi.yaml."""
    src = SOURCES["api.js"]
    calls = re.findall(r'request\(\s*[`"](/[^`"]*)[`"](?:,\s*\{[^}]*method:\s*"(\w+)")?', src)
    used = {((method or "GET"), re.sub(r"\$\{[^}]+\}", "{neo_reference_id}", path)) for path, method in calls}
    assert used and used <= operations(), used - operations()
    assert "fetch(" in src and all("fetch(" not in s for n, s in SOURCES.items() if n != "api.js")


def test_api_text_never_goes_into_innerHTML():
    for name, src in SOURCES.items():
        assert not re.search(r"\.(inner|outer)HTML\s*[+]?=|insertAdjacentHTML\(|document\.write\(", src), name


def test_static_files_ship_with_the_package():
    pyproject = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text()
    assert '"planetary_defense.web" = ["static/*"]' in pyproject
