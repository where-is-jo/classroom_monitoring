"""자연어 탐지 검색 라우터.

같은 서비스 함수를 화면(page_router)과 API(api_router)가 함께 쓴다.

검색을 `POST`로 두는 이유는 질문이 본문에 들어가기 때문이다. api-convention이
"부작용 없는 조회에 본문이 필요하면 동작을 리소스로 만들고 POST를 쓴다"고 정했다.
생성이 아니므로 상태 코드는 200이다.

## 화면도 질문을 본문으로 받는다

예전에는 `GET /llm-search?q=...`였다. **질문이 주소창에 그대로 남는다.** 이 기능의
질문에는 사람 이름과 찾는 시각이 담기므로("어제 16시 30분 박무현"), 주소가 브라우저
방문 기록·북마크·화면 공유·중계 서버 접근 로그에 그대로 쌓인다. 조회이지 생성이
아니라 GET이 형식상 맞지만, 여기서는 그 대가가 크다.

그래서 화면도 폼을 `POST`로 보낸다. 이 저장소에서 화면이 폼을 POST하는 첫 자리다 —
다른 화면의 쓰기는 JS가 API를 부르는 방식이다. 여기서 그 방식을 쓰지 않는 이유는
**결과를 그리는 일이 통째로 JS로 넘어가기 때문이다.** 지금은 서비스가 만든 요약
문장과 판정을 Jinja2가 그대로 받아 쓰는데, JS로 옮기면 같은 해석이 두 벌이 된다.

`q` 파라미터는 남기지 않는다. 남겨 두면 그 경로로 들어온 질문이 그대로 주소에
남아, 고치려던 문제가 절반만 고쳐진다.
"""

from __future__ import annotations

from typing import Final

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import Response

from ..shared.dependencies import get_llm_search_service
from ..shared.templating import templates
from .errors import (
    LlmSearchDisabledError,
    LlmSearchPlanInvalidError,
    LlmSearchPlannerUnavailableError,
)
from .models import SortOrder
from .planning import MAX_LIMIT
from .schemas import LlmSearchRequest, LlmSearchResponse
from .service import LlmSearchService

api_router = APIRouter(prefix="/api/v1", tags=["llm-search"])
page_router = APIRouter(tags=["llm-search-pages"])

# 화면은 결과를 쪽으로 나눠 보여주므로 상한만큼 받아 온다. 예전에는 20이었는데,
# 그 20이 곧 한 화면이라 "더 있는지"를 알 방법이 없었다. 지금은 20건이 한 쪽이고
# 상한(100)까지 받아 쪽 번호로 넘긴다.
_SCREEN_LIMIT: Final = MAX_LIMIT


@api_router.post("/llm-searches", response_model=LlmSearchResponse)
def search_detections(
    payload: LlmSearchRequest,
    service: LlmSearchService | None = Depends(get_llm_search_service),
) -> LlmSearchResponse:
    if service is None:
        raise LlmSearchDisabledError()
    outcome = service.search(payload.question, limit=payload.limit, sort=payload.sort)
    return LlmSearchResponse.from_domain(payload.question, outcome)


@page_router.get("/llm-search")
def llm_search_page(
    request: Request,
    service: LlmSearchService | None = Depends(get_llm_search_service),
) -> Response:
    """질문 입력 화면. **여기서는 검색하지 않는다.**

    질문은 아래 `POST`가 본문으로 받는다. 주소로 질문을 받는 경로를 남기지 않는 것이
    이 화면을 둘로 나눈 이유다.
    """
    return _render(request, service=service, question="", sort=SortOrder.TIME_DESC)


@page_router.post("/llm-search")
def llm_search_submit(
    request: Request,
    question: str = Form(default="", max_length=200),
    sort: str = Form(default=SortOrder.TIME_DESC.value, max_length=32),
    service: LlmSearchService | None = Depends(get_llm_search_service),
) -> Response:
    """질문을 본문으로 받아 결과까지 그린다.

    폼 값이 비어 있으면 검색하지 않고 안내 화면으로 되돌린다. 빈 질문을 LLM에게
    보내면 모델이 아무 계획이나 지어내고, 사용자는 자기가 묻지 않은 결과를 받는다.
    """
    return _render(request, service=service, question=question.strip(), sort=_sort_order(sort))


def _sort_order(value: str) -> SortOrder:
    """폼이 보낸 정렬 값을 도메인 값으로 바꾼다. **모르는 값이면 기본값으로 돌린다.**

    API는 규격에 없는 값을 422로 거절하지만 화면은 그러지 않는다. 여기서 오류를
    내면 사용자가 고칠 수 있는 것이 없다 — 정렬은 사용자가 타이핑하는 값이 아니라
    고르는 값이라, 이상한 값이 왔다면 그건 사용자의 잘못이 아니다. 질문까지 함께
    버리는 대신 최신순으로 보여주고 검색은 진행한다.
    """
    try:
        return SortOrder(value)
    except ValueError:
        return SortOrder.TIME_DESC


def _render(
    request: Request, *, service: LlmSearchService | None, question: str, sort: SortOrder
) -> Response:
    """질문·해석·결과를 한 화면에 보여준다.

    화면이 구분해야 하는 상태가 여섯이다. 기능 비활성 / 질문 전 / 결과 없음 /
    LLM에 닿지 못함 / 조건으로 바꾸지 못함 / 이미지 확인 실패. **묶어서 보여주면
    사용자가 할 수 있는 일이 무엇인지 알 수 없다** — 질문을 고쳐야 하는지 관리자를
    불러야 하는지, 아니면 여기서는 아예 안 되는 일인지가 각각 다르다.

    비활성일 때 **200으로 안내 화면을 돌려준다.** 오류 페이지로 보내면 "고장"으로
    읽히는데, 실제로는 이 환경의 정상 상태다. API가 503을 쓰는 것과 갈리는 지점이다.

    GET과 POST가 같은 화면을 그린다. 둘의 차이는 질문을 받았는지 하나뿐이라, 분기를
    라우터 함수에 복사하면 상태 여섯 개의 처리가 두 벌이 된다.
    """
    enabled = service is not None
    outcome = None
    planner_error = False
    plan_error = False

    if service is not None and question:
        try:
            outcome = service.search(question, limit=_SCREEN_LIMIT, sort=sort)
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
            "sort": sort.value,
            "asked": enabled and bool(question),
            "outcome": outcome,
            "planner_error": planner_error,
            "plan_error": plan_error,
        },
    )
