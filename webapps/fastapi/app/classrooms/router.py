"""강의실 좌석 조회 API와 단일 화면."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from ..shared.config import Settings
from ..shared.dependencies import get_classroom_service, get_settings
from ..shared.templating import templates
from .schemas import ClassroomListResponse, OccupancySummaryResponse
from .service import ClassroomService

api_router = APIRouter(prefix="/api/v1/classrooms", tags=["classrooms"])
page_router = APIRouter(prefix="/classrooms", tags=["classroom-pages"])


def _paging(limit: int | None, offset: int, settings: Settings) -> tuple[int, int]:
    return min(limit or settings.page_size_default, settings.page_size_max), offset


@api_router.get("", response_model=ClassroomListResponse)
def list_classrooms(
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    service: ClassroomService = Depends(get_classroom_service),
    settings: Settings = Depends(get_settings),
) -> ClassroomListResponse:
    resolved_limit, resolved_offset = _paging(limit, offset, settings)
    return ClassroomListResponse.from_page(
        service.list_classrooms(limit=resolved_limit, offset=resolved_offset),
        resolved_limit,
        resolved_offset,
    )


@api_router.get("/{classroom_id}/occupancy", response_model=OccupancySummaryResponse)
def occupancy_summary(
    classroom_id: str,
    service: ClassroomService = Depends(get_classroom_service),
) -> OccupancySummaryResponse:
    return OccupancySummaryResponse.from_domain(service.occupancy_summary(classroom_id))


@page_router.get("")
def classrooms_page(
    request: Request,
    classroom_id: str | None = Query(default=None, max_length=200),
    service: ClassroomService = Depends(get_classroom_service),
    settings: Settings = Depends(get_settings),
) -> Response:
    page = service.list_classrooms(limit=settings.page_size_max, offset=0)
    selected = classroom_id
    if selected is None and page.items:
        selected = page.items[0].id
    summary = None if selected is None else service.occupancy_summary(selected)
    return templates.TemplateResponse(
        request=request,
        name="classrooms/list.html",
        context={
            "classrooms": page.items,
            "selected_classroom_id": selected or "",
            "summary": summary,
        },
    )
