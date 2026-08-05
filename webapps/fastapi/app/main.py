"""애플리케이션 진입점.

라우터 등록, 정적 파일 마운트, 예외 핸들러를 여기서 조립한다.
비즈니스 로직을 두지 않는다.
"""

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

from .auth.dependencies import login_redirect
from .auth.errors import PageAuthenticationRequired
from .auth.router import api_router as auth_api_router
from .auth.router import page_router as auth_page_router
from .events.router import api_router as events_api_router
from .events.router import page_router as events_page_router
from .employees.router import admin_page_router as employees_admin_page_router
from .employees.router import api_router as employees_api_router
from .employees.router import development_api_router as employee_development_api_router
from .employees.router import (
    development_page_router as employee_development_page_router,
)
from .employees.router import evaluation_api_router as employee_evaluation_api_router
from .employees.router import page_router as employees_page_router
from .shared.config import Settings
from .shared.dependencies import (
    close_data_store,
    get_settings,
    initialize_data_store,
    verify_readiness,
)
from .shared.errors import DomainError, ErrorDetail, ErrorResponse
from .shared.schemas import HealthResponse, ReadinessResponse
from .shared.templating import STATIC_DIR, templates
from .users.router import api_router as users_api_router
from .users.router import page_router as users_page_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    try:
        initialize_data_store()
        yield
    finally:
        close_data_store()


app = FastAPI(
    title="Smart Office Monitoring",
    description="이벤트 조회, 인증·사용자 관리, 직원 상태 API와 화면",
    version="0.1.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(auth_page_router)
app.include_router(events_page_router)
app.include_router(users_page_router)
app.include_router(
    events_api_router,
    responses={
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
_AUTH_ERROR_RESPONSES = {
    400: {"model": ErrorResponse},
    401: {"model": ErrorResponse},
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    429: {"model": ErrorResponse},
    500: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}
app.include_router(auth_api_router, responses=_AUTH_ERROR_RESPONSES)
app.include_router(users_api_router, responses=_AUTH_ERROR_RESPONSES)


def include_employee_routers(application: FastAPI, settings: Settings) -> None:
    """일반 직원 기능은 항상, mock 입력 기능은 허용 환경에만 등록한다."""
    application.include_router(employees_page_router)
    application.include_router(employees_admin_page_router)
    application.include_router(employees_api_router, responses=_AUTH_ERROR_RESPONSES)
    application.include_router(
        employee_evaluation_api_router,
        responses=_AUTH_ERROR_RESPONSES,
    )
    if settings.mock_inputs_enabled:
        application.include_router(employee_development_page_router)
        application.include_router(
            employee_development_api_router,
            responses=_AUTH_ERROR_RESPONSES,
        )


include_employee_routers(app, get_settings())


def _wants_json(request: Request) -> bool:
    """API 경로면 JSON 오류 본문, 화면 경로면 오류 페이지를 낸다."""
    return request.url.path.startswith(("/api/", "/health"))


def _error_content(
    *,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return ErrorResponse(
        error=ErrorDetail(code=code, message=message, details=details or {})
    ).model_dump()


def _error_page(
    request: Request,
    *,
    status_code: int,
    message: str,
) -> Response:
    template_name = "errors/404.html" if status_code == 404 else "errors/error.html"
    return templates.TemplateResponse(
        request=request,
        name=template_name,
        context={"message": message, "status_code": status_code},
        status_code=status_code,
    )


@app.exception_handler(DomainError)
def handle_domain_error(request: Request, exc: DomainError) -> Response:
    """서비스 계층의 예외를 HTTP 응답으로 바꾼다.

    내부 정보(스택 트레이스, 내부 경로)를 응답에 넣지 않는다.
    """
    if _wants_json(request):
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_content(
                code=exc.code,
                message=exc.message,
                details=exc.details,
            ),
        )
    return _error_page(
        request,
        status_code=exc.status_code,
        message=exc.message,
    )


@app.exception_handler(PageAuthenticationRequired)
def handle_page_authentication_required(
    _: Request,
    exc: PageAuthenticationRequired,
) -> RedirectResponse:
    return RedirectResponse(url=login_redirect(exc.return_to), status_code=303)


@app.exception_handler(RequestValidationError)
def handle_validation_error(request: Request, exc: RequestValidationError) -> Response:
    message = "요청 값이 올바르지 않습니다."
    if not _wants_json(request):
        return _error_page(request, status_code=422, message=message)
    sanitized_errors = [
        {"location": list(error["loc"]), "type": error["type"]}
        for error in exc.errors()
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
    codes = {401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND"}
    messages = {
        401: "인증이 필요합니다.",
        403: "이 요청을 수행할 권한이 없습니다.",
        404: "요청한 페이지를 찾을 수 없습니다.",
    }
    code = codes.get(exc.status_code, "HTTP_ERROR")
    message = messages.get(exc.status_code, "요청을 처리할 수 없습니다.")
    if _wants_json(request):
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_content(code=code, message=message),
            headers=exc.headers,
        )
    return _error_page(request, status_code=exc.status_code, message=message)


@app.exception_handler(Exception)
def handle_unexpected_error(request: Request, exc: Exception) -> Response:
    # 예외 문자열에는 접속 주소가 포함될 수 있어 유형만 기록한다.
    logger.error("Unhandled application error type=%s", type(exc).__name__)
    message = "서버에서 요청을 처리하지 못했습니다."
    if _wants_json(request):
        return JSONResponse(
            status_code=500,
            content=_error_content(code="INTERNAL_ERROR", message=message),
        )
    return _error_page(request, status_code=500, message=message)


@app.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse(url="/events")


@app.get("/health", tags=["system"], response_model=HealthResponse)
def health() -> HealthResponse:
    """기동 여부 확인용. 의존 서비스 상태는 확인하지 않는다."""
    return HealthResponse(status="ok")


@app.get(
    "/health/ready",
    tags=["system"],
    response_model=ReadinessResponse,
    responses={503: {"model": ErrorResponse}},
)
def readiness(_: None = Depends(verify_readiness)) -> ReadinessResponse:
    """현재 저장소 mode의 의존성이 요청을 처리할 준비가 됐는지 확인한다."""
    return ReadinessResponse(status="ready")
