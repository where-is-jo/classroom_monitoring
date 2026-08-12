"""Minimal learning monitoring application assembly."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .classrooms.router import api_router as classroom_api_router
from .classrooms.router import page_router as classroom_page_router
from .shared.dependencies import (
    close_data_store,
    get_settings,
    initialize_data_store,
    verify_readiness,
)
from .shared.errors import DomainError, ErrorDetail, ErrorResponse
from .shared.schemas import HealthResponse, ReadinessResponse
from .shared.templating import DEMO_ASSET_DIR, STATIC_DIR, templates
from .student_monitoring.router import api_router as student_api_router
from .student_monitoring.router import internal_router as student_internal_router
from .snapshots.router import api_router as snapshot_api_router
from .snapshots.router import page_router as snapshot_page_router
from .video_monitoring.router import api_router as monitoring_api_router
from .video_monitoring.router import page_router as monitoring_page_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    try:
        initialize_data_store()
        yield
    finally:
        close_data_store()


app = FastAPI(
    title="Learning Monitoring",
    description="Classroom seat monitoring, real-time monitoring, natural language search API and screens",
    version="0.3.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
if get_settings().demo_mode_enabled:
    app.mount("/demo-assets", StaticFiles(directory=str(DEMO_ASSET_DIR)), name="demo-assets")
app.include_router(classroom_page_router, include_in_schema=False)
app.include_router(monitoring_page_router, include_in_schema=False)
app.include_router(snapshot_page_router, include_in_schema=False)

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    500: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}
app.include_router(classroom_api_router, responses=_ERROR_RESPONSES)
app.include_router(monitoring_api_router, responses=_ERROR_RESPONSES)
app.include_router(student_internal_router, responses=_ERROR_RESPONSES)
app.include_router(student_api_router, responses=_ERROR_RESPONSES)
app.include_router(snapshot_api_router, responses=_ERROR_RESPONSES)


def _wants_json(request: Request) -> bool:
    return request.url.path.startswith(("/api/", "/health", "/internal"))


def _error_content(
    *, code: str, message: str, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    return ErrorResponse(
        error=ErrorDetail(code=code, message=message, details=details or {})
    ).model_dump()


def _error_page(request: Request, *, status_code: int, message: str) -> Response:
    template_name = "errors/404.html" if status_code == 404 else "errors/error.html"
    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context={"message": message, "status_code": status_code},
        status_code=status_code,
    )


@app.exception_handler(DomainError)
def handle_domain_error(request: Request, exc: DomainError) -> Response:
    if _wants_json(request):
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_content(code=exc.code, message=exc.message, details=exc.details),
        )
    return _error_page(request, status_code=exc.status_code, message=exc.message)


@app.exception_handler(RequestValidationError)
def handle_validation_error(request: Request, exc: RequestValidationError) -> Response:
    message = "Request value is invalid."
    if not _wants_json(request):
        return _error_page(request, status_code=422, message=message)
    sanitized_errors = [
        {"location": list(error["loc"]), "type": error["type"]} for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content=_error_content(
            code="VALIDATION_ERROR",
            message=message,
            details={"errors": sanitized_errors},
        ),
    )


@app.exception_handler(StarletteHTTPException)
def handle_http_error(request: Request, exc: StarletteHTTPException) -> Response:
    is_not_found = exc.status_code == 404
    message = "Requested page not found." if is_not_found else "Request could not be processed."
    code = "NOT_FOUND" if is_not_found else "HTTP_ERROR"
    if _wants_json(request):
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_content(code=code, message=message),
            headers=exc.headers,
        )
    return _error_page(request, status_code=exc.status_code, message=message)


@app.exception_handler(Exception)
def handle_unexpected_error(request: Request, exc: Exception) -> Response:
    logger.error("Unhandled application error type=%s", type(exc).__name__)
    message = "Server could not process the request."
    if _wants_json(request):
        return JSONResponse(
            status_code=500,
            content=_error_content(code="INTERNAL_ERROR", message=message),
        )
    return _error_page(request, status_code=500, message=message)


@app.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse(url="/classrooms")


@app.get("/health", tags=["system"], response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get(
    "/health/ready",
    tags=["system"],
    response_model=ReadinessResponse,
    responses={503: {"model": ErrorResponse}},
)
def readiness(_: None = Depends(verify_readiness)) -> ReadinessResponse:
    return ReadinessResponse(status="ready")