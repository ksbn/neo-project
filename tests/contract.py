"""``openapi.yaml`` as an executable check: JSON Schema validation of requests and responses.

OpenAPI 3.1 schemas are JSON Schema 2020-12, so the whole document is registered as one
resource and any part of it is validated by JSON pointer. :class:`ContractClient` wraps
FastAPI's TestClient so that *every* response a test receives is checked against the
operation's declared statuses, headers and body schema.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import yaml
from fastapi.testclient import TestClient
from httpx2 import Response
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

SPEC_PATH = Path(__file__).resolve().parent.parent / "openapi.yaml"
SPEC: dict[str, Any] = yaml.safe_load(SPEC_PATH.read_text())
_URI = "urn:astraea:openapi"
_REGISTRY: Registry = Registry().with_resource(_URI, Resource.from_contents(SPEC, default_specification=DRAFT202012))
BASE = SPEC["servers"][0]["url"]  # /api/v1


def _escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


@cache
def validator(pointer: str) -> Draft202012Validator:
    """A validator for the schema at ``pointer`` (e.g. ``/components/schemas/ObjectPage``)."""
    return Draft202012Validator({"$ref": f"{_URI}#{pointer}"}, registry=_REGISTRY,
                                format_checker=Draft202012Validator.FORMAT_CHECKER)


def schema_errors(pointer: str, instance: Any) -> list[str]:
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
            for e in validator(pointer).iter_errors(instance)]


def operations() -> set[tuple[str, str]]:
    """Every (METHOD, path template) the contract declares."""
    methods = {"get", "post", "put", "patch", "delete"}
    return {(m.upper(), path) for path, item in SPEC["paths"].items() for m in item if m in methods}


def _resolve(node: dict[str, Any], pointer: str) -> tuple[dict[str, Any], str]:
    """Follow a local ``$ref`` (one level is all the contract uses for responses and parameters)."""
    if "$ref" in node:
        ref = node["$ref"].removeprefix("#")
        target: Any = SPEC
        for part in ref.strip("/").split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        return target, ref
    return node, pointer


def response_spec(method: str, path: str, status: int) -> tuple[dict[str, Any], str]:
    op_pointer = f"/paths/{_escape(path)}/{method.lower()}"
    responses = SPEC["paths"][path][method.lower()]["responses"]
    assert str(status) in responses, f"{method} {path} returned {status}, which the contract does not declare"
    return _resolve(responses[str(status)], f"{op_pointer}/responses/{status}")


def parameter_schema(method: str, path: str, name: str) -> dict[str, Any]:
    for p in SPEC["paths"][path][method.lower()].get("parameters", []):
        param, _ = _resolve(p, "")
        if param["name"] == name:
            schema, _ = _resolve(param["schema"], "")
            return schema
    raise KeyError(name)


def check_response(method: str, path: str, response: Response) -> None:
    """The status is declared, the declared headers are present, and the body matches the schema."""
    spec, pointer = response_spec(method, path, response.status_code)
    for header in spec.get("headers", {}):
        assert header.lower() in response.headers, f"{method} {path} {response.status_code}: missing {header}"
    content = spec.get("content", {}).get("application/json")
    if content is not None:
        errors = schema_errors(f"{pointer}/content/application~1json/schema", response.json())
        assert not errors, f"{method} {path} {response.status_code} breaks the contract: {errors}"
        if response.status_code >= 400:
            assert response.json()["request_id"] == response.headers["X-Request-ID"]


class ContractClient:
    """TestClient calls by path template; every response is checked against the contract."""

    def __init__(self, client: TestClient) -> None:
        self.client = client

    def request(self, method: str, path: str, *, path_params: dict[str, str] | None = None,
                **kwargs: Any) -> Response:
        url = BASE + path.format(**(path_params or {}))
        response = self.client.request(method, url, **kwargs)
        check_response(method, path, response)
        return response

    def get(self, path: str, **kwargs: Any) -> Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Response:
        return self.request("POST", path, **kwargs)
