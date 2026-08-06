"""STAFF/ADMIN-only pages and APIs for local/dev synthetic video demos."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from ..auth.dependencies import (
    CSRF_COOKIE,
    get_current_page_user,
    get_current_user,
    require_csrf,
)
from ..auth.errors import PermissionDeniedError
from ..shared.dependencies import get_video_demo_service
from ..shared.templating import templates
from ..users.models import User, UserRole
from .models import DemoStreamStatus
from .schemas import (
    DemoStreamListResponse,
    DemoStreamResponse,
    VideoSearchRequest,
    VideoSearchResponse,
)
from .service import VideoDemoService

api_router = APIRouter(prefix="/api/v1", tags=["video-demo"])
page_router = APIRouter(tags=["video-demo-pages"])


def require_video_user(actor: User = Depends(get_current_user)) -> User:
    if actor.role not in {UserRole.STAFF, UserRole.ADMIN}:
        raise PermissionDeniedError()
    return actor


def require_video_page_user(actor: User = Depends(get_current_page_user)) -> User:
    if actor.role not in {UserRole.STAFF, UserRole.ADMIN}:
        raise PermissionDeniedError()
    return actor


@api_router.get("/video-streams", response_model=DemoStreamListResponse)
def list_video_streams(
    q: str | None = Query(default=None, max_length=100),
    classroom_id: str | None = Query(default=None, max_length=128),
    stream_status: DemoStreamStatus | None = Query(default=None, alias="status"),
    _: User = Depends(require_video_user),
    service: VideoDemoService = Depends(get_video_demo_service),
) -> DemoStreamListResponse:
    items = service.list_streams(
        search=q, classroom_id=classroom_id, status=stream_status
    )
    return DemoStreamListResponse(
        items=[DemoStreamResponse.from_domain(item) for item in items], total=len(items)
    )


@api_router.get("/video-streams/{stream_id}", response_model=DemoStreamResponse)
def get_video_stream(
    stream_id: str,
    _: User = Depends(require_video_user),
    service: VideoDemoService = Depends(get_video_demo_service),
) -> DemoStreamResponse:
    return DemoStreamResponse.from_domain(service.get_stream(stream_id))


@api_router.post("/video-searches", response_model=VideoSearchResponse)
def search_videos(
    payload: VideoSearchRequest,
    _: None = Depends(require_csrf),
    __: User = Depends(require_video_user),
    service: VideoDemoService = Depends(get_video_demo_service),
) -> VideoSearchResponse:
    return VideoSearchResponse.from_domain(
        service.search_videos(
            payload.query,
            classroom_id=payload.classroom_id,
            from_at=payload.from_at,
            to_at=payload.to_at,
            limit=payload.limit,
        )
    )


@page_router.get("/monitoring")
def monitoring_page(
    request: Request,
    q: str | None = Query(default=None, max_length=100),
    classroom_id: str | None = Query(default=None, max_length=128),
    stream_status: DemoStreamStatus | None = Query(default=None, alias="status"),
    actor: User = Depends(require_video_page_user),
    service: VideoDemoService = Depends(get_video_demo_service),
) -> Response:
    feeds = service.list_streams(
        search=q, classroom_id=classroom_id, status=stream_status
    )
    return templates.TemplateResponse(
        request=request,
        name="video_monitoring/monitoring.html",
        context=_page_context(
            request,
            actor,
            feeds=feeds,
            classroom_options=service.classroom_options(),
            statuses=list(DemoStreamStatus),
            selected_query=q or "",
            selected_classroom_id=classroom_id or "",
            selected_status=stream_status,
            current_time=service.current_time(),
        ),
    )


@page_router.get("/video-search")
def video_search_page(
    request: Request,
    query: str | None = Query(default=None, min_length=1, max_length=200),
    classroom_id: str | None = Query(default=None, max_length=128),
    from_at: datetime | None = Query(default=None, alias="from"),
    to_at: datetime | None = Query(default=None, alias="to"),
    limit: int = Query(default=20, ge=1, le=50),
    actor: User = Depends(require_video_page_user),
    service: VideoDemoService = Depends(get_video_demo_service),
) -> Response:
    results = None
    if query is not None:
        results = service.search_videos(
            query,
            classroom_id=classroom_id,
            from_at=_page_datetime(from_at),
            to_at=_page_datetime(to_at),
            limit=limit,
        )
    return templates.TemplateResponse(
        request=request,
        name="video_monitoring/search.html",
        context=_page_context(
            request,
            actor,
            results=results,
            classroom_options=service.classroom_options(),
            selected_query=query or "",
            selected_classroom_id=classroom_id or "",
            selected_from=from_at,
            selected_to=to_at,
            selected_limit=limit,
        ),
    )


def _page_datetime(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=ZoneInfo("Asia/Seoul"))


def _page_context(request: Request, actor: User, **values: object) -> dict[str, object]:
    return {
        "current_user": actor,
        "csrf_token": request.cookies.get(CSRF_COOKIE, ""),
        "can_view_employees": True,
        "can_manage_users": actor.role == UserRole.ADMIN,
        "can_manage_employees": actor.role == UserRole.ADMIN,
        "show_employee_dev_tools": False,
        "show_notification_dev_tools": False,
        **values,
    }
