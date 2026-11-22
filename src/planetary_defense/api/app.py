"""The REST API (``openapi.yaml``, version 1) and the dashboard that consumes it.

:func:`create_app` is the composition root: it receives a connection factory (tests pass
their throwaway-schema connection; :func:`main` opens PostgreSQL from ``.env``), wires
the repositories into :class:`application.catalog_service.CatalogService`, and turns
domain errors into ``ErrorResponse`` bodies. FastAPI's generated schema is switched off:
the hand-written contract is served at ``/api/openapi.yaml`` instead.

No ``from __future__ import annotations`` here: FastAPI must evaluate the endpoint annotations,
which refer to dependencies defined inside :func:`create_app`.
"""

import logging
import re
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Final

import psycopg
from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from planetary_defense.api.errors import ApiError, RateLimiter, classify, error_response, from_domain
from planetary_defense.api.schemas import (
    ErrorDetail,
    NeoId,
    ObjectDetailOut,
    ObjectPage,
    ObjectSummary,
    Pagination,
    SeverityName,
    WatchlistEntryCreate,
    WatchlistEntryOut,
)
from planetary_defense.application.catalog_service import CatalogService
from planetary_defense.domain.catalog import ObjectQuery, Priority, SortKey, WatchRequest
from planetary_defense.domain.errors import NeoError
from planetary_defense.domain.models import Severity
from planetary_defense.infrastructure.catalog_repository import ObjectRepository, WatchlistRepository
from planetary_defense.infrastructure.repository import AsteroidRepository, OrbitRepository

logger = logging.getLogger(__name__)

PREFIX: Final = "/api/v1"
ROOT: Final = Path(__file__).resolve().parents[3]
CONTRACT: Final = ROOT / "openapi.yaml"
STATIC: Final = Path(__file__).resolve().parent.parent / "web" / "static"
_REQUEST_ID: Final = re.compile(r"^[A-Za-z0-9_.-]{8,64}$")

ConnectionFactory = Callable[[], AbstractContextManager[psycopg.Connection]]


def create_app(
    open_connection: ConnectionFactory,
    *,
    read_limiter: RateLimiter | None = None,
    write_limiter: RateLimiter | None = None,
    today: Callable[[], date] = date.today,
) -> FastAPI:
    reads = read_limiter or RateLimiter(capacity=240, per_second=4)
    writes = write_limiter or RateLimiter(capacity=20, per_second=0.2)
    app = FastAPI(title="ASTRAEA Planetary Defense API", openapi_url=None, docs_url=None, redoc_url=None)

    # ------------------------------------------------------------ plumbing

    @app.middleware("http")
    async def request_id(request: Request, call_next: Callable[[Request], Any]) -> Response:
        incoming = request.headers.get("X-Request-ID", "")
        request.state.request_id = incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex[:16]
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(ApiError)
    async def on_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(request, exc)

    @app.exception_handler(NeoError)
    async def on_domain_error(request: Request, exc: NeoError) -> JSONResponse:
        err = from_domain(exc)
        if err.status >= 500:
            logger.warning("%s %s failed [%s]: %s", request.method, request.url.path, request.state.request_id, exc)
        return error_response(request, err)

    @app.exception_handler(RequestValidationError)
    async def on_invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(request, classify(exc.errors(), body=False))

    @app.exception_handler(StarletteHTTPException)
    async def on_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "NOT_FOUND" if exc.status_code == 404 else "BAD_REQUEST"
        return error_response(request, ApiError(exc.status_code, code, str(exc.detail)))

    @app.exception_handler(Exception)
    async def on_bug(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error [%s]", getattr(request.state, "request_id", "unknown"))
        return error_response(request, ApiError(500, "INTERNAL_ERROR", "An unexpected error occurred."))

    def service() -> Iterator[CatalogService]:
        with open_connection() as conn:
            yield CatalogService(ObjectRepository(conn), AsteroidRepository(conn), OrbitRepository(conn),
                                 WatchlistRepository(conn))

    def client_of(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def limit_reads(request: Request) -> None:
        reads.check(client_of(request))

    def limit_writes(request: Request) -> None:
        writes.check(client_of(request))

    async def raw_body(request: Request) -> bytes:
        return await request.body()

    # ------------------------------------------------------------ routes

    @app.get(f"{PREFIX}/objects", dependencies=[Depends(limit_reads)])
    def list_objects(
        svc: Annotated[CatalogService, Depends(service)],
        q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
        hazardous: bool | None = None,
        max_distance_ld: Annotated[float | None, Query(gt=0, le=10_000)] = None,
        min_velocity_km_s: Annotated[float | None, Query(ge=0, le=1_000)] = None,
        min_severity: SeverityName | None = None,
        approach_from: date | None = None,
        approach_to: date | None = None,
        sort: SortKey = "-approach_date",
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    ) -> JSONResponse:
        try:
            query = ObjectQuery(
                q=q, hazardous=hazardous, max_distance_ld=max_distance_ld, min_velocity_km_s=min_velocity_km_s,
                min_severity=Severity[min_severity.upper()] if min_severity else None,
                approach_from=approach_from, approach_to=approach_to, sort=sort, limit=limit, offset=offset,
            )
        except ValueError as exc:  # parameters that are fine alone but not together
            raise ApiError(400, "BAD_REQUEST", "The request has invalid parameters.",
                           [ErrorDetail(field="query.approach_from", reason=str(exc))]) from exc
        page = svc.list_objects(query)
        body = ObjectPage(items=[ObjectSummary.of(r) for r in page.rows],
                          pagination=Pagination.of(page.total, limit, offset))
        return JSONResponse(body.model_dump(mode="json"))

    @app.get(PREFIX + "/objects/{neo_reference_id}", dependencies=[Depends(limit_reads)])
    def get_object(
        svc: Annotated[CatalogService, Depends(service)], neo_reference_id: Annotated[NeoId, PathParam()]
    ) -> JSONResponse:
        detail = svc.get_object(neo_reference_id)
        return JSONResponse(ObjectDetailOut.of_detail(detail, today()).model_dump(mode="json"))

    @app.post(f"{PREFIX}/watchlist", status_code=201, dependencies=[Depends(limit_writes)])
    def create_watchlist_entry(
        svc: Annotated[CatalogService, Depends(service)],
        body: Annotated[bytes, Depends(raw_body)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", min_length=8, max_length=128,
                                                      pattern=r"^[A-Za-z0-9_.:-]+$")] = None,
    ) -> JSONResponse:
        try:
            payload = WatchlistEntryCreate.model_validate_json(body or b"")
        except ValidationError as exc:
            raise classify(exc.errors(), body=True) from exc
        request = WatchRequest(neo_reference_id=payload.neo_reference_id, priority=Priority(payload.priority),
                               alert_threshold_ld=payload.alert_threshold_ld, note=payload.note,
                               tags=tuple(payload.tags))
        result = svc.watch(request, idempotency_key)
        return JSONResponse(
            WatchlistEntryOut.of(result.entry).model_dump(mode="json"), status_code=201,
            headers={"Location": f"{PREFIX}/objects/{result.entry.neo_reference_id}",
                     "Idempotent-Replayed": "true" if result.replayed else "false"},
        )

    # ------------------------------------------------------------ contract and dashboard

    @app.get("/api/openapi.yaml", include_in_schema=False)
    def contract() -> FileResponse:
        return FileResponse(CONTRACT, media_type="application/yaml")

    @app.get("/api/docs", include_in_schema=False)
    def docs() -> HTMLResponse:
        return HTMLResponse(
            '<!doctype html><html><head><meta charset="utf-8"><title>ASTRAEA API</title></head><body>'
            '<redoc spec-url="/api/openapi.yaml"></redoc>'
            '<script src="https://cdn.jsdelivr.net/npm/redoc@2.5.1/bundles/redoc.standalone.js"></script>'
            "</body></html>"
        )

    if STATIC.is_dir():
        app.mount("/", StaticFiles(directory=STATIC, html=True), name="dashboard")
    return app


def main() -> None:  # pragma: no cover - starts a server
    """``neo-api``: serve the API and the dashboard on http://localhost:8000."""
    import argparse
    from contextlib import contextmanager

    import uvicorn

    from planetary_defense.config import Settings
    from planetary_defense.infrastructure.db import connect

    parser = argparse.ArgumentParser(description="Serve the ASTRAEA REST API and dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    settings = Settings.load()

    @contextmanager
    def open_connection() -> Iterator[psycopg.Connection]:
        with connect(settings.db(), connect_timeout=3, application_name="neo-api") as conn:
            yield conn

    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    uvicorn.run(create_app(open_connection), host=args.host, port=args.port)
