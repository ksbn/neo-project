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
    """Every API path the UI requests, as a template, is an operation of openapi.yaml."""
    src = SOURCES["api.js"]
    calls = re.findall(r'request\(\s*[`"](/[^`"]*)[`"](?:,\s*\{[^}]*method:\s*"(\w+)")?', src)
    used = {((method or "GET"), re.sub(r"\$\{[^}]+\}", "{neo_reference_id}", path)) for path, method in calls}
    boot = re.findall(r"fetch\(`/api/v1(/[^`?]*)", SOURCES["viewport-boot.js"])  # the 3D page's own GET
    used |= {("GET", re.sub(r"\$\{[^}]+\}", "{neo_reference_id}", path)) for path in boot}
    assert ("GET", "/objects/{neo_reference_id}/trajectory") in used
    assert used <= operations(), used - operations()
    fetching = {n for n, s in SOURCES.items() if "fetch(" in s}
    assert fetching == {"api.js", "viewport-boot.js"}, fetching  # no other side doors


def test_the_viewport_page_boots_the_shared_scene(api):
    page = api.client.get("/viewport.html?id=2524522")
    assert page.status_code == 200 and 'import "./viewport-boot.js";' in page.text
    assert "three@0.170.0" in page.text and "__" not in page.text.split("<body>")[1]  # every placeholder filled
    scene = api.client.get("/viewport-scene.js")
    assert scene.status_code == 200 and scene.headers["content-type"].startswith("text/javascript")
    assert "__TEXTURE_BASE__" not in scene.text and "function buildEarthView" in scene.text


def test_api_text_never_goes_into_innerHTML():
    for name, src in SOURCES.items():
        assert not re.search(r"\.(inner|outer)HTML\s*[+]?=|insertAdjacentHTML\(|document\.write\(", src), name


def test_static_files_ship_with_the_package():
    pyproject = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text()
    assert '"planetary_defense.web" = ["static/*"]' in pyproject
