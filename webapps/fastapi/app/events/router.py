"""이벤트 HTTP 진입점.

화면(`page_router`)과 JSON API(`api_router`)가 **같은 서비스 함수를 호출한다.**
라우터는 입력 변환 → 서비스 호출 → 응답 변환까지만 한다. 판단은 서비스에서 끝난다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response

from ..auth.dependencies import CSRF_COOKIE, can_manage_users, get_optional_page_user
from ..shared.config import Settings
from ..shared.dependencies import get_event_service, get_settings
from ..shared.templating import templates
from ..users.models import User
from .schemas import EventListResponse, EventResponse
from .service import EventService

page_router = APIRouter(tags=["events-pages"])
api_router = APIRouter(prefix="/api/v1/events", tags=["events"])


def _resolve_paging(
    limit: int | None,
    offset: int,
    settings: Settings,
) -> tuple[int, int]:
    """페이지네이션 값을 설정 기준으로 정리한다.

    상한을 두지 않으면 전체 조회 요청이 들어온다.
    """
    resolved_limit = settings.page_size_default if limit is None else limit
    resolved_limit = max(1, min(resolved_limit, settings.page_size_max))
    return resolved_limit, max(0, offset)


# --------------------------------------------------------------------------
# 화면
# --------------------------------------------------------------------------


@page_router.get("/events")
def events_page(
    request: Request,
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    service: EventService = Depends(get_event_service),
    settings: Settings = Depends(get_settings),
    current_user: User | None = Depends(get_optional_page_user),
) -> Response:
    resolved_limit, resolved_offset = _resolve_paging(limit, offset, settings)
    page = service.list_events(limit=resolved_limit, offset=resolved_offset)
    return templates.TemplateResponse(
        request=request,
        name="events/list.html",
        context={
            "page": page,
            "limit": resolved_limit,
            "offset": resolved_offset,
            "has_prev": resolved_offset > 0,
            "has_next": resolved_offset + resolved_limit < page.total,
            "current_user": current_user,
            "csrf_token": request.cookies.get(CSRF_COOKIE, ""),
            "can_manage_users": can_manage_users(current_user),
            "can_view_employees": current_user is not None,
            "can_manage_employees": can_manage_users(current_user),
        },
    )


@page_router.get("/events/{event_id}")
def event_detail_page(
    request: Request,
    event_id: str,
    service: EventService = Depends(get_event_service),
    current_user: User | None = Depends(get_optional_page_user),
) -> Response:
    # 대상이 없으면 EventNotFoundError가 올라가고 main.py의 핸들러가 404 화면을 낸다.
    summary = service.get_event(event_id)
    return templates.TemplateResponse(
        request=request,
        name="events/detail.html",
        context={
            "summary": summary,
            "current_user": current_user,
            "csrf_token": request.cookies.get(CSRF_COOKIE, ""),
            "can_manage_users": can_manage_users(current_user),
            "can_view_employees": current_user is not None,
            "can_manage_employees": can_manage_users(current_user),
        },
    )


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


@api_router.get("", response_model=EventListResponse)
def list_events(
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    service: EventService = Depends(get_event_service),
    settings: Settings = Depends(get_settings),
) -> EventListResponse:
    resolved_limit, resolved_offset = _resolve_paging(limit, offset, settings)
    page = service.list_events(limit=resolved_limit, offset=resolved_offset)
    return EventListResponse.from_page(page, limit=resolved_limit, offset=resolved_offset)


@api_router.get("/{event_id}", response_model=EventResponse)
def get_event(
    event_id: str,
    service: EventService = Depends(get_event_service),
) -> EventResponse:
    return EventResponse.from_summary(service.get_event(event_id))
