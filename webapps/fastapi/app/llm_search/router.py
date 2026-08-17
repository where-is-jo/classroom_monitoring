"""자연어 탐지 검색 라우터.

같은 서비스 함수를 화면(page_router)과 API(api_router)가 함께 쓴다.

검색을 `POST`로 두는 이유는 질문이 본문에 들어가기 때문이다. api-convention이
"부작용 없는 조회에 본문이 필요하면 동작을 리소스로 만들고 POST를 쓴다"고 정했고
`POST /api/v1/video-searches`가 같은 형태다. 생성이 아니므로 상태 코드는 200이다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from ..shared.dependencies import get_llm_search_service
from ..shared.templating import templates
from .errors import (
    LlmSearchDisabledError,
    LlmSearchPlanInvalidError,
    LlmSearchPlannerUnavailableError,
)
from .schemas import LlmSearchRequest, LlmSearchResponse
from .service import LlmSearchService

api_router = APIRouter(prefix="/api/v1", tags=["llm-search"])
page_router = APIRouter(tags=["llm-search-pages"])


@api_router.post("/llm-searches", response_model=LlmSearchResponse)
def search_detections(
    payload: LlmSearchRequest,
    service: LlmSearchService | None = Depends(get_llm_search_service),
) -> LlmSearchResponse:
    if service is None:
        raise LlmSearchDisabledError()
    outcome = service.search(payload.question, limit=payload.limit)
    return LlmSearchResponse.from_domain(payload.question, outcome)


@page_router.get("/llm-search")
def llm_search_page(
    request: Request,
    q: str | None = Query(default=None, max_length=200),
    service: LlmSearchService | None = Depends(get_llm_search_service),
) -> Response:
    """질문·해석·결과를 한 화면에 보여준다.

    화면이 구분해야 하는 상태가 여섯이다. 기능 비활성 / 질문 전 / 결과 없음 /
    LLM에 닿지 못함 / 조건으로 바꾸지 못함 / 이미지 확인 실패. **묶어서 보여주면
    사용자가 할 수 있는 일이 무엇인지 알 수 없다** — 질문을 고쳐야 하는지 관리자를
    불러야 하는지, 아니면 여기서는 아예 안 되는 일인지가 각각 다르다.

    비활성일 때 **200으로 안내 화면을 돌려준다.** 오류 페이지로 보내면 "고장"으로
    읽히는데, 실제로는 이 환경의 정상 상태다. API가 503을 쓰는 것과 갈리는 지점이다.
    """
    enabled = service is not None
    question = (q or "").strip()
    outcome = None
    planner_error = False
    plan_error = False

    if service is not None and question:
        try:
            outcome = service.search(question, limit=20)
        except LlmSearchPlannerUnavailableError:
            planner_error = True
        except LlmSearchPlanInvalidError:
            plan_error = True

    return templates.TemplateResponse(
        request=request,
        name="llm_search/llm_search.html",
        context={
            "search_enabled": enabled,
            "question": question,
            "asked": enabled and bool(question),
            "outcome": outcome,
            "planner_error": planner_error,
            "plan_error": plan_error,
        },
    )
