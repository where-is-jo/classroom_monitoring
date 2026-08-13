"""강의실 좌석 조회 API와 단일 화면."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import RedirectResponse, Response, StreamingResponse

from ..shared.broadcaster import InMemoryBroadcaster
from ..shared.config import Settings
from ..shared.dependencies import (
    get_broadcaster,
    get_classroom_service,
    get_settings,
    get_student_monitoring_service,
    get_student_service,
)
from ..shared.templating import templates
from ..student_monitoring.service import StudentMonitoringService
from ..students.service import StudentService
from .errors import ClassroomNotFoundError, SeatNotFoundError
from .schemas import (
    ClassroomCreateRequest,
    ClassroomListResponse,
    ClassroomResponse,
    ClassroomUpdateRequest,
    OccupancySummaryResponse,
    SeatAssignmentListResponse,
    SeatAssignmentRequest,
    SeatAssignmentResponse,
    SeatCreateRequest,
    SeatListResponse,
    SeatResponse,
    SeatUpdateRequest,
)
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


@api_router.post("", response_model=ClassroomResponse, status_code=status.HTTP_201_CREATED)
def create_classroom(
    payload: ClassroomCreateRequest,
    response: Response,
    service: ClassroomService = Depends(get_classroom_service),
) -> ClassroomResponse:
    classroom = service.create_classroom(
        code=payload.code,
        name=payload.name,
        location=payload.location,
    )
    response.headers["Location"] = f"/api/v1/classrooms/{classroom.id}"
    return ClassroomResponse.from_domain(classroom)


@api_router.get("/{classroom_id}", response_model=ClassroomResponse)
def get_classroom(
    classroom_id: str,
    service: ClassroomService = Depends(get_classroom_service),
) -> ClassroomResponse:
    return ClassroomResponse.from_domain(service.get_classroom(classroom_id))


@api_router.put("/{classroom_id}", response_model=ClassroomResponse)
def update_classroom(
    classroom_id: str,
    payload: ClassroomUpdateRequest,
    service: ClassroomService = Depends(get_classroom_service),
) -> ClassroomResponse:
    classroom = service.update_classroom(
        classroom_id,
        code=payload.code,
        name=payload.name,
        location=payload.location,
        is_active=payload.is_active,
    )
    return ClassroomResponse.from_domain(classroom)


@api_router.delete("/{classroom_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_classroom(
    classroom_id: str,
    service: ClassroomService = Depends(get_classroom_service),
) -> Response:
    service.delete_classroom(classroom_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@api_router.post(
    "/{classroom_id}/seats",
    response_model=SeatResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_seat(
    classroom_id: str,
    payload: SeatCreateRequest,
    response: Response,
    service: ClassroomService = Depends(get_classroom_service),
) -> SeatResponse:
    seat = service.create_seat(
        classroom_id,
        code=payload.code,
        label=payload.label,
        geometry=None if payload.geometry is None else payload.geometry.to_domain(),
    )
    response.headers["Location"] = f"/api/v1/classrooms/{classroom_id}/seats/{seat.id}"
    return SeatResponse.from_domain(seat)


@api_router.get("/{classroom_id}/seats", response_model=SeatListResponse)
def list_seats(
    classroom_id: str,
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    service: ClassroomService = Depends(get_classroom_service),
    settings: Settings = Depends(get_settings),
) -> SeatListResponse:
    resolved_limit, resolved_offset = _paging(limit, offset, settings)
    return SeatListResponse.from_page(
        service.list_seats(classroom_id, limit=resolved_limit, offset=resolved_offset),
        resolved_limit,
        resolved_offset,
    )


@api_router.get("/{classroom_id}/seats/{seat_id}", response_model=SeatResponse)
def get_seat(
    classroom_id: str,
    seat_id: str,
    service: ClassroomService = Depends(get_classroom_service),
) -> SeatResponse:
    return SeatResponse.from_domain(service.get_seat(seat_id))


@api_router.put("/{classroom_id}/seats/{seat_id}", response_model=SeatResponse)
def update_seat(
    classroom_id: str,
    seat_id: str,
    payload: SeatUpdateRequest,
    service: ClassroomService = Depends(get_classroom_service),
) -> SeatResponse:
    seat = service.update_seat(
        seat_id,
        code=payload.code,
        label=payload.label,
        geometry=None if payload.geometry is None else payload.geometry.to_domain(),
        is_active=payload.is_active,
    )
    return SeatResponse.from_domain(seat)


@api_router.delete(
    "/{classroom_id}/seats/{seat_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_seat(
    classroom_id: str,
    seat_id: str,
    service: ClassroomService = Depends(get_classroom_service),
) -> Response:
    service.delete_seat(seat_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@api_router.put("/{classroom_id}/seats/{seat_id}/assignment", response_model=SeatAssignmentResponse)
def assign_student_to_seat(
    classroom_id: str,
    seat_id: str,
    payload: SeatAssignmentRequest,
    service: ClassroomService = Depends(get_classroom_service),
) -> SeatAssignmentResponse:
    """좌석에 학생을 지정한다."""
    info = service.assign_student(seat_id, payload.student_id)
    return SeatAssignmentResponse.from_domain(info)


@api_router.delete(
    "/{classroom_id}/seats/{seat_id}/assignment",
    status_code=status.HTTP_204_NO_CONTENT,
)
def unassign_student_from_seat(
    classroom_id: str,
    seat_id: str,
    service: ClassroomService = Depends(get_classroom_service),
) -> Response:
    """좌석-학생 지정을 해제한다."""
    service.unassign_student(seat_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@api_router.get("/{classroom_id}/seat-assignments", response_model=SeatAssignmentListResponse)
def list_seat_assignments(
    classroom_id: str,
    service: ClassroomService = Depends(get_classroom_service),
) -> SeatAssignmentListResponse:
    """강의실의 전체 좌석-학생 지정 현황을 조회한다."""
    infos = service.list_assignments(classroom_id)
    return SeatAssignmentListResponse(
        items=[SeatAssignmentResponse.from_domain(info) for info in infos]
    )


@api_router.get("/{classroom_id}/occupancy", response_model=OccupancySummaryResponse)
def occupancy_summary(
    classroom_id: str,
    service: ClassroomService = Depends(get_classroom_service),
) -> OccupancySummaryResponse:
    return OccupancySummaryResponse.from_domain(service.occupancy_summary(classroom_id))


@api_router.get("/{classroom_id}/occupancy-events")
async def stream_occupancy_events(
    classroom_id: str,
    service: ClassroomService = Depends(get_classroom_service),
    broadcaster: InMemoryBroadcaster = Depends(get_broadcaster),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """강의실 좌석 점유 상태를 실시간으로 전달하는 SSE 스트림.

    student_monitoring의 탐지 SSE와 같은 broadcaster를 구독하되,
    occupancy 타입이면서 요청한 강의실의 이벤트만 필터링해 내려준다.
    이벤트가 없으면 설정된 heartbeat 간격으로 heartbeat를 보내고,
    브라우저의 재연결 대기는 retry 지시로 설정된 시간으로 안내한다.
    """
    service.get_classroom(classroom_id)

    async def event_generator() -> AsyncIterator[str]:
        queue = broadcaster.subscribe()
        try:
            yield f"retry: {settings.sse_reconnection_timeout_seconds * 1000}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=settings.sse_heartbeat_interval_seconds
                    )
                    if (
                        event.get("type") == "occupancy"
                        and event.get("classroom_id") == classroom_id
                    ):
                        yield f"id: {event.get('event_id', '')}\n"
                        yield "event: occupancy\n"
                        yield f"data: {json.dumps(event)}\n\n"
                except TimeoutError:
                    yield ": heartbeat\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            broadcaster.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


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


@page_router.get("/create")
def classroom_create_page(request: Request) -> Response:
    return templates.TemplateResponse(
        request=request,
        name="classrooms/create.html",
        context={},
    )


@page_router.get("/{classroom_id}/edit")
def classroom_edit_page(
    classroom_id: str,
    request: Request,
    service: ClassroomService = Depends(get_classroom_service),
) -> Response:
    try:
        classroom = service.get_classroom(classroom_id)
    except ClassroomNotFoundError:
        return RedirectResponse(url="/classrooms", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(
        request=request,
        name="classrooms/edit.html",
        context={"classroom": classroom},
    )


@page_router.get("/{classroom_id}/seats")
def seats_page(
    classroom_id: str,
    request: Request,
    service: ClassroomService = Depends(get_classroom_service),
) -> Response:
    try:
        summary = service.occupancy_summary(classroom_id)
    except ClassroomNotFoundError:
        return RedirectResponse(url="/classrooms", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(
        request=request,
        name="classrooms/seats.html",
        context={
            "classroom": summary.classroom,
            "seats": summary.seats,
            "total": summary.total,
        },
    )


@page_router.get("/{classroom_id}/seats/create")
def seat_create_page(
    classroom_id: str,
    request: Request,
    service: ClassroomService = Depends(get_classroom_service),
) -> Response:
    try:
        classroom = service.get_classroom(classroom_id)
    except ClassroomNotFoundError:
        return RedirectResponse(url="/classrooms", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(
        request=request,
        name="classrooms/seat_edit.html",
        context={"classroom": classroom, "seat": None},
    )


@page_router.get("/{classroom_id}/seats/{seat_id}/edit")
def seat_edit_page(
    classroom_id: str,
    seat_id: str,
    request: Request,
    service: ClassroomService = Depends(get_classroom_service),
) -> Response:
    seats_url = f"/classrooms/{classroom_id}/seats"
    try:
        classroom = service.get_classroom(classroom_id)
        seat = service.get_seat(seat_id)
    except (ClassroomNotFoundError, SeatNotFoundError):
        return RedirectResponse(url=seats_url, status_code=status.HTTP_302_FOUND)
    if seat.classroom_id != classroom.id:
        return RedirectResponse(url=seats_url, status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(
        request=request,
        name="classrooms/seat_edit.html",
        context={"classroom": classroom, "seat": seat},
    )
