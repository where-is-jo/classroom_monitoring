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
    get_student_lookup,
    get_student_monitoring_service,
)
from ..shared.student_identity import StudentLookupPort, validate_list_active_args
from ..shared.templating import templates
from ..student_monitoring.service import StudentMonitoringService
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
        row=payload.row,
        column=payload.column,
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
    # 명시적으로 null이 전달된 경우에만 해제한다 (전달 안 된 필드는 갱신하지 않는다).
    unset_row = "row" in payload.model_fields_set and payload.row is None
    unset_column = "column" in payload.model_fields_set and payload.column is None

    seat = service.update_seat(
        seat_id,
        code=payload.code,
        label=payload.label,
        row=payload.row,
        column=payload.column,
        geometry=None if payload.geometry is None else payload.geometry.to_domain(),
        is_active=payload.is_active,
        unset_row=unset_row,
        unset_column=unset_column,
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
    # 좌석 배치도에 지정 학생 이름을 표시하기 위한 지정 현황 (seat_id → 지정 정보).
    assignments = (
        {}
        if selected is None
        else {info.seat_id: info for info in service.list_assignments(selected)}
    )
    return templates.TemplateResponse(
        request=request,
        name="classrooms/list.html",
        context={
            "classrooms": page.items,
            "selected_classroom_id": selected or "",
            "summary": summary,
            "assignments": assignments,
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
    # 행·열이 있는 좌석만 배치도에 표시한다 (REQ-006).
    grid_seats = [
        seat for seat in summary.seats if seat.row is not None and seat.column is not None
    ]
    # CSS Grid 크기 계산과 빈 셀 렌더링용 좌표 맵을 만든다.
    # 빈 강의실도 조작 가능한 최소 1x1 격자를 제공한다 (SPEC: max(1, current max)).
    max_row = max((seat.row for seat in grid_seats if seat.row is not None), default=1)
    max_column = max((seat.column for seat in grid_seats if seat.column is not None), default=1)
    seat_map = {(seat.row, seat.column): seat for seat in grid_seats}
    return templates.TemplateResponse(
        request=request,
        name="classrooms/seats.html",
        context={
            "classroom": summary.classroom,
            "seats": summary.seats,
            "total": summary.total,
            "grid_seats": grid_seats,
            "max_row": max_row,
            "max_column": max_column,
            "seat_map": seat_map,
        },
    )


@page_router.get("/any/seat-assignments")
def seat_assignments_page_any(
    service: ClassroomService = Depends(get_classroom_service),
) -> Response:
    """classroom_id 미지정 시 첫 번째 강의실로 리다이렉트."""
    page = service.list_classrooms(limit=1, offset=0)
    if not page.items:
        return RedirectResponse(url="/classrooms/create", status_code=status.HTTP_302_FOUND)
    return RedirectResponse(
        url=f"/classrooms/{page.items[0].id}/seat-assignments",
        status_code=status.HTTP_302_FOUND,
    )


@page_router.get("/{classroom_id}/seat-assignments")
def seat_assignments_page(
    classroom_id: str,
    request: Request,
    service: ClassroomService = Depends(get_classroom_service),
    student_lookup: StudentLookupPort = Depends(get_student_lookup),
    settings: Settings = Depends(get_settings),
) -> Response:
    """좌석-학생 지정 관리 페이지.

    좌석 목록과 지정 현황, 활성 학생 목록을 조합해 렌더링한다.
    지정/해제는 기존 JSON API를 classroom-admin.js가 fetch로 호출한다.
    """
    try:
        classroom = service.get_classroom(classroom_id)
    except ClassroomNotFoundError:
        return RedirectResponse(url="/classrooms", status_code=status.HTTP_302_FOUND)

    seats = service.list_all_seats(classroom_id)
    assignments_list = service.list_assignments(classroom_id)
    # seat_id를 키로 변환해 템플릿에서 좌석별 지정을 바로 조회한다.
    assignments = {info.seat_id: info for info in assignments_list}
    # 학생 선택 select에는 활성 학생만 노출한다 (UI-REQ-007).
    # 중립 조회 계약: limit은 1~page_size_max, offset은 0 이상이어야 한다.
    validate_list_active_args(
        limit=settings.page_size_max, offset=0, page_size_max=settings.page_size_max
    )
    students_page = student_lookup.list_active(limit=settings.page_size_max, offset=0)

    return templates.TemplateResponse(
        request=request,
        name="classrooms/seat_assignments.html",
        context={
            "classroom": classroom,
            "seats": seats,
            "assignments": assignments,
            "students": students_page.items,
        },
    )


@page_router.get("/any/student-states")
def student_states_page_any(
    service: ClassroomService = Depends(get_classroom_service),
) -> Response:
    """classroom_id 미지정 시 첫 번째 강의실로 리다이렉트."""
    page = service.list_classrooms(limit=1, offset=0)
    if not page.items:
        return RedirectResponse(url="/classrooms/create", status_code=status.HTTP_302_FOUND)
    return RedirectResponse(
        url=f"/classrooms/{page.items[0].id}/student-states",
        status_code=status.HTTP_302_FOUND,
    )


@page_router.get("/{classroom_id}/student-states")
def student_states_page(
    classroom_id: str,
    request: Request,
    service: ClassroomService = Depends(get_classroom_service),
    monitoring_service: StudentMonitoringService = Depends(get_student_monitoring_service),
    settings: Settings = Depends(get_settings),
) -> Response:
    """학생 상태 조회 페이지.

    강의실 목록(선택 드롭다운용)과 학생별 상태를 조합해 렌더링한다.
    상태 판정은 서비스 계층이 담당하고, 라우터는 조회·렌더링만 한다.
    """
    try:
        classroom = service.get_classroom(classroom_id)
    except ClassroomNotFoundError:
        return RedirectResponse(url="/classrooms", status_code=status.HTTP_302_FOUND)

    # 강의실 선택 드롭다운용 목록
    classrooms_page = service.list_classrooms(limit=settings.page_size_max, offset=0)
    # 학생 상태 조회 (TASK-A05 유보로 현재는 빈 목록)
    states = monitoring_service.list_student_states(classroom_id)

    return templates.TemplateResponse(
        request=request,
        name="classrooms/student_states.html",
        context={
            "classroom": classroom,
            "classrooms": classrooms_page.items,
            "states": states,
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
