"""애플리케이션 진입점.

라우터 등록, 정적 파일 마운트, 예외 핸들러를 여기서 조립한다.
비즈니스 로직을 두지 않는다.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .events.router import api_router, page_router
from .shared.errors import DomainError
from .shared.templating import STATIC_DIR, templates

app = FastAPI(
    title="Smart Office Monitoring",
    description="탐지 이벤트 조회 API와 관리자 화면",
    version="0.1.0",
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(page_router)
app.include_router(api_router)


def _wants_json(request: Request) -> bool:
    """API 경로면 JSON 오류 본문, 화면 경로면 오류 페이지를 낸다."""
    return request.url.path.startswith("/api/")


@app.exception_handler(DomainError)
def handle_domain_error(request: Request, exc: DomainError):
    """서비스 계층의 예외를 HTTP 응답으로 바꾼다.

    내부 정보(스택 트레이스, 내부 경로)를 응답에 넣지 않는다.
    """
    if _wants_json(request):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message, "details": {}}},
        )
    return templates.TemplateResponse(
        request=request,
        name="errors/404.html",
        context={"message": exc.message},
        status_code=exc.status_code,
    )


@app.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse(url="/events")


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    """기동 여부 확인용. 의존 서비스 상태는 확인하지 않는다."""
    return {"status": "ok"}
