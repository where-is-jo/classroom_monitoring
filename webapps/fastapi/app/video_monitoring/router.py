"""인증 없이 모니터링 조회와 규칙 기반 검색을 제공하는 라우터."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from ..shared.dependencies import get_video_demo_service
from ..shared.templating import templates
from .errors import VideoSearchInputError
from .models import DemoStreamStatus
from .schemas import (
    DemoStreamListResponse,
    DemoStreamResponse,
    VideoSearchRequest,
    VideoSearchResponse,
)
from .service import VideoDemoService

api_router = APIRouter(prefix="/api/v1", tags=["monitoring"])
page_router = APIRouter(tags=["monitoring-pages"])


@api_router.get("/video-streams", response_model=DemoStreamListResponse)
def list_video_streams(
    q: str | None = Query(default=None, max_length=100),
    classroom_id: str | None = Query(default=None, max_length=128),
    stream_status: DemoStreamStatus | None = Query(default=None, alias="status"),
    service: VideoDemoService = Depends(get_video_demo_service),
) -> DemoStreamListResponse:
    items = service.list_streams(search=q, classroom_id=classroom_id, status=stream_status)
    return DemoStreamListResponse(
        items=[DemoStreamResponse.from_domain(item) for item in items], total=len(items)
    )


@api_router.get("/video-streams/{stream_id}", response_model=DemoStreamResponse)
def get_video_stream(
    stream_id: str,
    service: VideoDemoService = Depends(get_video_demo_service),
) -> DemoStreamResponse:
    return DemoStreamResponse.from_domain(service.get_stream(stream_id))


@api_router.post("/video-searches", response_model=VideoSearchResponse)
def search_videos(
    payload: VideoSearchRequest,
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
    service: VideoDemoService = Depends(get_video_demo_service),
) -> Response:
    feeds = service.list_streams(search=q, classroom_id=classroom_id, status=stream_status)
    options = service.classroom_options()
    return templates.TemplateResponse(
        request=request,
        name="video_monitoring/monitoring.html",
        context={
            "feeds": feeds,
            "classroom_options": options,
            "statuses": list(DemoStreamStatus),
            "selected_query": q or "",
            "selected_classroom_id": classroom_id or "",
            "selected_status": stream_status,
            "current_time": service.current_time(),
            "demo_enabled": bool(options),
        },
    )


@page_router.get("/video-search")
def video_search_page(
    request: Request,
    query: str | None = Query(default=None, min_length=1, max_length=200),
    classroom_id: str | None = Query(default=None, max_length=128),
    from_at: str | None = Query(default=None, alias="from", max_length=40),
    to_at: str | None = Query(default=None, alias="to", max_length=40),
    limit: int = Query(default=20, ge=1, le=50),
    service: VideoDemoService = Depends(get_video_demo_service),
) -> Response:
    parsed_from = _page_datetime(from_at)
    parsed_to = _page_datetime(to_at)
    results = None
    if query is not None:
        results = service.search_videos(
            query,
            classroom_id=classroom_id,
            from_at=parsed_from,
            to_at=parsed_to,
            limit=limit,
        )
    options = service.classroom_options()
    return templates.TemplateResponse(
        request=request,
        name="video_monitoring/search.html",
        context={
            "results": results,
            "classroom_options": options,
            "selected_query": query or "",
            "selected_classroom_id": classroom_id or "",
            "selected_from": parsed_from,
            "selected_to": parsed_to,
            "selected_limit": limit,
            "demo_enabled": bool(options),
        },
    )


def _page_datetime(value: str | None) -> datetime | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise VideoSearchInputError("검색 시각 형식이 올바르지 않습니다.") from None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=ZoneInfo("Asia/Seoul"))
    return parsed
