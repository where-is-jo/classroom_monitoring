"""Classroom JSON APIs and server-rendered management pages."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Annotated
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from ..auth.dependencies import (
    CSRF_COOKIE,
    get_current_page_user,
    get_current_user,
    request_ip_fingerprint,
    require_admin,
    require_csrf,
    require_page_admin,
)
from ..shared.config import Settings
from ..shared.dependencies import get_classroom_service, get_settings
from ..shared.errors import DomainError
from ..shared.templating import templates
from ..users.models import ADMIN_ROLES, User
from .errors import ClassroomInputError
from .models import (
    AfterHoursAlertStatus,
    ClassroomSchedule,
    CreateClassroomCommand,
    CreateSeatCommand,
    RecordSeatObservationBatchCommand,
    ReplaceSchedulesCommand,
    ResolveAfterHoursAlertCommand,
    SeatGeometry,
    SeatObservation,
    UpdateClassroomCommand,
    UpdateSeatCommand,
)
from .schemas import (
    AfterHoursAlertListResponse,
    AfterHoursAlertResponse,
    AlertResolveForm,
    ClassroomCreateForm,
    ClassroomUpdateForm,
    ClassroomListResponse,
    ClassroomResponse,
    CreateClassroomRequest,
    CreateSeatRequest,
    MockSeatObservationBatchRequest,
    MutationRequest,
    MutationForm,
    OccupancyHistoryListResponse,
    OccupancySummaryResponse,
    ReplaceSchedulesRequest,
    ResolveAlertRequest,
    ScheduleLinesForm,
    ScheduleSchema,
    SeatCreateForm,
    SeatUpdateForm,
    SeatListResponse,
    SeatObservationBatchResponse,
    SeatObservationLinesForm,
    SeatResponse,
    UpdateClassroomRequest,
    UpdateSeatRequest,
)
from .service import ClassroomService

classroom_api_router = APIRouter(prefix="/api/v1/classrooms", tags=["classrooms"])
seat_api_router = APIRouter(prefix="/api/v1/seats", tags=["classrooms"])
alert_api_router = APIRouter(prefix="/api/v1/after-hours-alerts", tags=["classrooms"])
development_api_router = APIRouter(
    prefix="/api/v1/mock-seat-observations", tags=["development"]
)
page_router = APIRouter(prefix="/classrooms", tags=["classroom-pages"])
admin_page_router = APIRouter(prefix="/admin/classrooms", tags=["classroom-pages"])
alert_page_router = APIRouter(prefix="/admin/alerts", tags=["classroom-pages"])
development_page_router = APIRouter(
    prefix="/admin/dev-tools/seat-observations", tags=["development-pages"]
)


def _paging(limit: int | None, offset: int, settings: Settings) -> tuple[int, int]:
    return min(limit or settings.page_size_default, settings.page_size_max), offset


@classroom_api_router.get("", response_model=ClassroomListResponse)
def list_classrooms(
    include_inactive: bool = False,
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    actor: User = Depends(get_current_user),
    service: ClassroomService = Depends(get_classroom_service),
    settings: Settings = Depends(get_settings),
) -> ClassroomListResponse:
    resolved_limit, resolved_offset = _paging(limit, offset, settings)
    return ClassroomListResponse.from_page(
        service.list_classrooms(
            actor,
            include_inactive=include_inactive,
            limit=resolved_limit,
            offset=resolved_offset,
        ),
        resolved_limit,
        resolved_offset,
    )


@classroom_api_router.post(
    "", response_model=ClassroomResponse, status_code=status.HTTP_201_CREATED
)
def create_classroom(
    payload: CreateClassroomRequest,
    response: Response,
    _: None = Depends(require_csrf),
    actor: User = Depends(require_admin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: ClassroomService = Depends(get_classroom_service),
) -> ClassroomResponse:
    classroom = service.create_classroom(
        actor,
        CreateClassroomCommand(
            code=payload.code,
            name=payload.name,
            location=payload.location,
            timezone=payload.timezone,
            after_hours_grace_minutes=payload.after_hours_grace_minutes,
            operation_id=str(payload.operation_id),
        ),
        ip_fingerprint=ip_fingerprint,
    )
    response.headers["Location"] = f"/api/v1/classrooms/{classroom.id}"
    return ClassroomResponse.from_domain(classroom)


@classroom_api_router.get("/{classroom_id}", response_model=ClassroomResponse)
def get_classroom(
    classroom_id: str,
    actor: User = Depends(get_current_user),
    service: ClassroomService = Depends(get_classroom_service),
) -> ClassroomResponse:
    return ClassroomResponse.from_domain(service.get_classroom(actor, classroom_id))


@classroom_api_router.patch("/{classroom_id}", response_model=ClassroomResponse)
def update_classroom(
    classroom_id: str,
    payload: UpdateClassroomRequest,
    _: None = Depends(require_csrf),
    actor: User = Depends(require_admin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: ClassroomService = Depends(get_classroom_service),
) -> ClassroomResponse:
    return ClassroomResponse.from_domain(
        service.update_classroom(
            actor,
            UpdateClassroomCommand(
                classroom_id=classroom_id,
                code=payload.code,
                name=payload.name,
                location=payload.location,
                timezone=payload.timezone,
                after_hours_grace_minutes=payload.after_hours_grace_minutes,
                expected_version=payload.expected_version,
                operation_id=str(payload.operation_id),
            ),
            ip_fingerprint=ip_fingerprint,
        )
    )


@classroom_api_router.delete(
    "/{classroom_id}", status_code=status.HTTP_204_NO_CONTENT
)
def deactivate_classroom(
    classroom_id: str,
    payload: MutationRequest,
    _: None = Depends(require_csrf),
    actor: User = Depends(require_admin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: ClassroomService = Depends(get_classroom_service),
) -> Response:
    service.deactivate_classroom(
        actor,
        classroom_id,
        expected_version=payload.expected_version,
        operation_id=str(payload.operation_id),
        ip_fingerprint=ip_fingerprint,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@classroom_api_router.get(
    "/{classroom_id}/schedules", response_model=list[ScheduleSchema]
)
def get_schedules(
    classroom_id: str,
    actor: User = Depends(get_current_user),
    service: ClassroomService = Depends(get_classroom_service),
) -> list[ScheduleSchema]:
    classroom = service.get_classroom(actor, classroom_id)
    return [
        ScheduleSchema(
            day_of_week=item.day_of_week,
            opens_at=item.opens_at,
            closes_at=item.closes_at,
        )
        for item in classroom.schedules
    ]


@classroom_api_router.put(
    "/{classroom_id}/schedules", response_model=ClassroomResponse
)
def replace_schedules(
    classroom_id: str,
    payload: ReplaceSchedulesRequest,
    _: None = Depends(require_csrf),
    actor: User = Depends(require_admin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: ClassroomService = Depends(get_classroom_service),
) -> ClassroomResponse:
    return ClassroomResponse.from_domain(
        service.replace_schedules(
            actor,
            ReplaceSchedulesCommand(
                classroom_id=classroom_id,
                schedules=tuple(item.to_domain() for item in payload.schedules),
                expected_version=payload.expected_version,
                operation_id=str(payload.operation_id),
            ),
            ip_fingerprint=ip_fingerprint,
        )
    )


@classroom_api_router.get(
    "/{classroom_id}/seats", response_model=SeatListResponse
)
def list_seats(
    classroom_id: str,
    include_inactive: bool = False,
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    actor: User = Depends(get_current_user),
    service: ClassroomService = Depends(get_classroom_service),
    settings: Settings = Depends(get_settings),
) -> SeatListResponse:
    resolved_limit, resolved_offset = _paging(limit, offset, settings)
    return SeatListResponse.from_page(
        service.list_seats(
            actor,
            classroom_id,
            include_inactive=include_inactive,
            limit=resolved_limit,
            offset=resolved_offset,
        ),
        resolved_limit,
        resolved_offset,
    )


@classroom_api_router.post(
    "/{classroom_id}/seats",
    response_model=SeatResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_seat(
    classroom_id: str,
    payload: CreateSeatRequest,
    response: Response,
    _: None = Depends(require_csrf),
    actor: User = Depends(require_admin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: ClassroomService = Depends(get_classroom_service),
) -> SeatResponse:
    seat = service.create_seat(
        actor,
        CreateSeatCommand(
            classroom_id=classroom_id,
            code=payload.code,
            label=payload.label,
            geometry=None if payload.geometry is None else payload.geometry.to_domain(),
            operation_id=str(payload.operation_id),
        ),
        ip_fingerprint=ip_fingerprint,
    )
    response.headers["Location"] = f"/api/v1/seats/{seat.id}"
    return SeatResponse.from_domain(seat)


@seat_api_router.patch("/{seat_id}", response_model=SeatResponse)
def update_seat(
    seat_id: str,
    payload: UpdateSeatRequest,
    _: None = Depends(require_csrf),
    actor: User = Depends(require_admin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: ClassroomService = Depends(get_classroom_service),
) -> SeatResponse:
    return SeatResponse.from_domain(
        service.update_seat(
            actor,
            UpdateSeatCommand(
                seat_id=seat_id,
                code=payload.code,
                label=payload.label,
                geometry=(
                    None if payload.geometry is None else payload.geometry.to_domain()
                ),
                expected_version=payload.expected_version,
                operation_id=str(payload.operation_id),
            ),
            ip_fingerprint=ip_fingerprint,
        )
    )


@seat_api_router.delete("/{seat_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_seat(
    seat_id: str,
    payload: MutationRequest,
    _: None = Depends(require_csrf),
    actor: User = Depends(require_admin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: ClassroomService = Depends(get_classroom_service),
) -> Response:
    service.deactivate_seat(
        actor,
        seat_id,
        expected_version=payload.expected_version,
        operation_id=str(payload.operation_id),
        ip_fingerprint=ip_fingerprint,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@classroom_api_router.get(
    "/{classroom_id}/occupancy", response_model=OccupancySummaryResponse
)
def occupancy_summary(
    classroom_id: str,
    actor: User = Depends(get_current_user),
    service: ClassroomService = Depends(get_classroom_service),
) -> OccupancySummaryResponse:
    return OccupancySummaryResponse.from_domain(
        service.occupancy_summary(actor, classroom_id)
    )


@classroom_api_router.get(
    "/{classroom_id}/occupancy-history",
    response_model=OccupancyHistoryListResponse,
)
def occupancy_history(
    classroom_id: str,
    seat_id: str | None = None,
    from_time: datetime | None = Query(default=None, alias="from"),
    to_time: datetime | None = Query(default=None, alias="to"),
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    actor: User = Depends(require_admin),
    service: ClassroomService = Depends(get_classroom_service),
    settings: Settings = Depends(get_settings),
) -> OccupancyHistoryListResponse:
    resolved_limit, resolved_offset = _paging(limit, offset, settings)
    return OccupancyHistoryListResponse.from_page(
        service.list_occupancy_history(
            actor,
            classroom_id,
            seat_id=seat_id,
            from_time=from_time,
            to_time=to_time,
            limit=resolved_limit,
            offset=resolved_offset,
        ),
        resolved_limit,
        resolved_offset,
    )


@development_api_router.post(
    "", response_model=SeatObservationBatchResponse, status_code=status.HTTP_201_CREATED
)
def record_mock_seat_observations(
    payload: MockSeatObservationBatchRequest,
    _: None = Depends(require_csrf),
    actor: User = Depends(require_admin),
    service: ClassroomService = Depends(get_classroom_service),
) -> SeatObservationBatchResponse:
    return SeatObservationBatchResponse.from_domain(
        service.record_mock_observation_batch(
            actor,
            RecordSeatObservationBatchCommand(
                event_id=str(payload.event_id),
                classroom_id=payload.classroom_id,
                observed_at=payload.observed_at,
                observations=tuple(
                    SeatObservation(
                        seat_id=item.seat_id,
                        occupied=item.occupied,
                        confidence=item.confidence,
                    )
                    for item in payload.seats
                ),
            ),
        )
    )


@alert_api_router.get("", response_model=AfterHoursAlertListResponse)
def list_after_hours_alerts(
    alert_status: AfterHoursAlertStatus | None = Query(default=None, alias="status"),
    classroom_id: str | None = None,
    business_date: date | None = Query(default=None, alias="date"),
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    actor: User = Depends(require_admin),
    service: ClassroomService = Depends(get_classroom_service),
    settings: Settings = Depends(get_settings),
) -> AfterHoursAlertListResponse:
    resolved_limit, resolved_offset = _paging(limit, offset, settings)
    return AfterHoursAlertListResponse.from_page(
        service.list_alerts(
            actor,
            status=alert_status,
            classroom_id=classroom_id,
            business_date=business_date,
            limit=resolved_limit,
            offset=resolved_offset,
        ),
        resolved_limit,
        resolved_offset,
    )


@alert_api_router.patch("/{alert_id}", response_model=AfterHoursAlertResponse)
def resolve_after_hours_alert(
    alert_id: str,
    payload: ResolveAlertRequest,
    _: None = Depends(require_csrf),
    actor: User = Depends(require_admin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: ClassroomService = Depends(get_classroom_service),
) -> AfterHoursAlertResponse:
    return AfterHoursAlertResponse.from_domain(
        service.resolve_alert(
            actor,
            ResolveAfterHoursAlertCommand(
                alert_id=alert_id,
                expected_version=payload.expected_version,
                operation_id=str(payload.operation_id),
            ),
            ip_fingerprint=ip_fingerprint,
        )
    )


@page_router.get("")
def classrooms_page(
    request: Request,
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    actor: User = Depends(get_current_page_user),
    service: ClassroomService = Depends(get_classroom_service),
    settings: Settings = Depends(get_settings),
):
    resolved_limit, resolved_offset = _paging(limit, offset, settings)
    page = service.list_classrooms(
        actor,
        include_inactive=False,
        limit=resolved_limit,
        offset=resolved_offset,
    )
    summaries = [service.occupancy_summary(actor, item.id) for item in page.items]
    return templates.TemplateResponse(
        request=request,
        name="classrooms/list.html",
        context=_page_context(
            request,
            actor,
            page=page,
            summaries=summaries,
            limit=resolved_limit,
            offset=resolved_offset,
            has_prev=resolved_offset > 0,
            has_next=resolved_offset + resolved_limit < page.total,
        ),
    )


@page_router.get("/{classroom_id}")
def classroom_detail_page(
    request: Request,
    classroom_id: str,
    actor: User = Depends(get_current_page_user),
    service: ClassroomService = Depends(get_classroom_service),
):
    summary = service.occupancy_summary(actor, classroom_id)
    return templates.TemplateResponse(
        request=request,
        name="classrooms/detail.html",
        context=_page_context(request, actor, summary=summary),
    )


@admin_page_router.get("")
def admin_classrooms_page(
    request: Request,
    actor: User = Depends(require_page_admin),
    service: ClassroomService = Depends(get_classroom_service),
    settings: Settings = Depends(get_settings),
):
    return _render_admin_classrooms(
        request, actor=actor, service=service, settings=settings
    )


@admin_page_router.post("")
def create_classroom_page(
    request: Request,
    form: Annotated[ClassroomCreateForm, Form()],
    _: None = Depends(require_csrf),
    actor: User = Depends(require_page_admin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: ClassroomService = Depends(get_classroom_service),
    settings: Settings = Depends(get_settings),
):
    try:
        service.create_classroom(
            actor,
            CreateClassroomCommand(
                code=form.code,
                name=form.name,
                location=form.location,
                timezone=form.timezone,
                after_hours_grace_minutes=form.after_hours_grace_minutes,
                operation_id=str(form.operation_id),
            ),
            ip_fingerprint=ip_fingerprint,
        )
    except DomainError as exc:
        return _render_admin_classrooms(
            request,
            actor=actor,
            service=service,
            settings=settings,
            error=exc.message,
            status_code=exc.status_code,
        )
    return RedirectResponse("/admin/classrooms", status_code=status.HTTP_303_SEE_OTHER)


@admin_page_router.post("/{classroom_id}/schedules")
def replace_schedules_page(
    request: Request,
    classroom_id: str,
    form: Annotated[ScheduleLinesForm, Form()],
    _: None = Depends(require_csrf),
    actor: User = Depends(require_page_admin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: ClassroomService = Depends(get_classroom_service),
    settings: Settings = Depends(get_settings),
):
    try:
        schedules = _parse_schedule_lines(form.schedule_lines)
        service.replace_schedules(
            actor,
            ReplaceSchedulesCommand(
                classroom_id=classroom_id,
                schedules=schedules,
                expected_version=form.expected_version,
                operation_id=str(form.operation_id),
            ),
            ip_fingerprint=ip_fingerprint,
        )
    except DomainError as exc:
        return _render_admin_classrooms(
            request,
            actor=actor,
            service=service,
            settings=settings,
            error=exc.message,
            status_code=exc.status_code,
        )
    return RedirectResponse("/admin/classrooms", status_code=status.HTTP_303_SEE_OTHER)


@admin_page_router.post("/{classroom_id}/update")
def update_classroom_page(
    request: Request,
    classroom_id: str,
    form: Annotated[ClassroomUpdateForm, Form()],
    _: None = Depends(require_csrf),
    actor: User = Depends(require_page_admin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: ClassroomService = Depends(get_classroom_service),
    settings: Settings = Depends(get_settings),
):
    try:
        service.update_classroom(
            actor,
            UpdateClassroomCommand(
                classroom_id=classroom_id,
                code=form.code,
                name=form.name,
                location=form.location,
                timezone=form.timezone,
                after_hours_grace_minutes=form.after_hours_grace_minutes,
                expected_version=form.expected_version,
                operation_id=str(form.operation_id),
            ),
            ip_fingerprint=ip_fingerprint,
        )
    except DomainError as exc:
        return _render_admin_classrooms(
            request,
            actor=actor,
            service=service,
            settings=settings,
            error=exc.message,
            status_code=exc.status_code,
        )
    return RedirectResponse("/admin/classrooms", status_code=status.HTTP_303_SEE_OTHER)


@admin_page_router.post("/{classroom_id}/deactivate")
def deactivate_classroom_page(
    request: Request,
    classroom_id: str,
    form: Annotated[MutationForm, Form()],
    _: None = Depends(require_csrf),
    actor: User = Depends(require_page_admin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: ClassroomService = Depends(get_classroom_service),
    settings: Settings = Depends(get_settings),
):
    try:
        service.deactivate_classroom(
            actor,
            classroom_id,
            expected_version=form.expected_version,
            operation_id=str(form.operation_id),
            ip_fingerprint=ip_fingerprint,
        )
    except DomainError as exc:
        return _render_admin_classrooms(
            request,
            actor=actor,
            service=service,
            settings=settings,
            error=exc.message,
            status_code=exc.status_code,
        )
    return RedirectResponse("/admin/classrooms", status_code=status.HTTP_303_SEE_OTHER)


@admin_page_router.post("/{classroom_id}/seats")
def create_seat_page(
    request: Request,
    classroom_id: str,
    form: Annotated[SeatCreateForm, Form()],
    _: None = Depends(require_csrf),
    actor: User = Depends(require_page_admin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: ClassroomService = Depends(get_classroom_service),
    settings: Settings = Depends(get_settings),
):
    try:
        service.create_seat(
            actor,
            CreateSeatCommand(
                classroom_id=classroom_id,
                code=form.code,
                label=form.label,
                geometry=_form_geometry(form),
                operation_id=str(form.operation_id),
            ),
            ip_fingerprint=ip_fingerprint,
        )
    except DomainError as exc:
        return _render_admin_classrooms(
            request,
            actor=actor,
            service=service,
            settings=settings,
            error=exc.message,
            status_code=exc.status_code,
        )
    return RedirectResponse("/admin/classrooms", status_code=status.HTTP_303_SEE_OTHER)


@admin_page_router.post("/{classroom_id}/seats/{seat_id}/update")
def update_seat_page(
    request: Request,
    classroom_id: str,
    seat_id: str,
    form: Annotated[SeatUpdateForm, Form()],
    _: None = Depends(require_csrf),
    actor: User = Depends(require_page_admin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: ClassroomService = Depends(get_classroom_service),
    settings: Settings = Depends(get_settings),
):
    try:
        _require_page_seat_membership(
            service, actor, classroom_id=classroom_id, seat_id=seat_id, settings=settings
        )
        service.update_seat(
            actor,
            UpdateSeatCommand(
                seat_id=seat_id,
                code=form.code,
                label=form.label,
                geometry=_form_geometry(form),
                expected_version=form.expected_version,
                operation_id=str(form.operation_id),
            ),
            ip_fingerprint=ip_fingerprint,
        )
    except DomainError as exc:
        return _render_admin_classrooms(
            request,
            actor=actor,
            service=service,
            settings=settings,
            error=exc.message,
            status_code=exc.status_code,
        )
    return RedirectResponse("/admin/classrooms", status_code=status.HTTP_303_SEE_OTHER)


@admin_page_router.post("/{classroom_id}/seats/{seat_id}/deactivate")
def deactivate_seat_page(
    request: Request,
    classroom_id: str,
    seat_id: str,
    form: Annotated[MutationForm, Form()],
    _: None = Depends(require_csrf),
    actor: User = Depends(require_page_admin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: ClassroomService = Depends(get_classroom_service),
    settings: Settings = Depends(get_settings),
):
    try:
        _require_page_seat_membership(
            service, actor, classroom_id=classroom_id, seat_id=seat_id, settings=settings
        )
        service.deactivate_seat(
            actor,
            seat_id,
            expected_version=form.expected_version,
            operation_id=str(form.operation_id),
            ip_fingerprint=ip_fingerprint,
        )
    except DomainError as exc:
        return _render_admin_classrooms(
            request,
            actor=actor,
            service=service,
            settings=settings,
            error=exc.message,
            status_code=exc.status_code,
        )
    return RedirectResponse("/admin/classrooms", status_code=status.HTTP_303_SEE_OTHER)


@alert_page_router.get("")
def alerts_page(
    request: Request,
    alert_status: AfterHoursAlertStatus | None = Query(default=None, alias="status"),
    actor: User = Depends(require_page_admin),
    service: ClassroomService = Depends(get_classroom_service),
    settings: Settings = Depends(get_settings),
):
    return _render_alerts(
        request,
        actor=actor,
        service=service,
        settings=settings,
        alert_status=alert_status,
    )


@alert_page_router.post("/{alert_id}/resolve")
def resolve_alert_page(
    request: Request,
    alert_id: str,
    form: Annotated[AlertResolveForm, Form()],
    _: None = Depends(require_csrf),
    actor: User = Depends(require_page_admin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: ClassroomService = Depends(get_classroom_service),
    settings: Settings = Depends(get_settings),
):
    try:
        service.resolve_alert(
            actor,
            ResolveAfterHoursAlertCommand(
                alert_id=alert_id,
                expected_version=form.expected_version,
                operation_id=str(form.operation_id),
            ),
            ip_fingerprint=ip_fingerprint,
        )
    except DomainError as exc:
        return _render_alerts(
            request,
            actor=actor,
            service=service,
            settings=settings,
            error=exc.message,
            status_code=exc.status_code,
        )
    return RedirectResponse("/admin/alerts", status_code=status.HTTP_303_SEE_OTHER)


@development_page_router.post("")
def record_seat_observations_page(
    request: Request,
    form: Annotated[SeatObservationLinesForm, Form()],
    _: None = Depends(require_csrf),
    actor: User = Depends(require_page_admin),
    service: ClassroomService = Depends(get_classroom_service),
):
    try:
        result = service.record_mock_observation_batch(
            actor,
            RecordSeatObservationBatchCommand(
                event_id=str(form.event_id),
                classroom_id=form.classroom_id,
                observed_at=form.observed_at,
                observations=_parse_seat_lines(form.seat_lines),
            ),
        )
    except DomainError as exc:
        return RedirectResponse(
            f"/admin/dev-tools?seat_result={quote(exc.message)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    message = (
        f"좌석 {result.processed_count}건 반영, 상태 변경 {result.changed_count}건, "
        f"경고 {result.alert_count}건"
    )
    return RedirectResponse(
        f"/admin/dev-tools?seat_result={quote(message)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _render_admin_classrooms(
    request: Request,
    *,
    actor: User,
    service: ClassroomService,
    settings: Settings,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
):
    page = service.list_classrooms(
        actor, include_inactive=True, limit=settings.page_size_max, offset=0
    )
    entries = [
        {
            "classroom": item,
            "seats": service.list_seats(
                actor,
                item.id,
                include_inactive=True,
                limit=settings.page_size_max,
                offset=0,
            ).items,
            "schedule_lines": "\n".join(
                f"{value.day_of_week},{value.opens_at.strftime('%H:%M')},{value.closes_at.strftime('%H:%M')}"
                for value in item.schedules
            ),
            "schedule_operation_id": str(uuid4()),
            "seat_operation_id": str(uuid4()),
            "update_operation_id": str(uuid4()),
            "deactivate_operation_id": str(uuid4()),
            "seat_update_operation_ids": {
                seat.id: str(uuid4())
                for seat in service.list_seats(
                    actor,
                    item.id,
                    include_inactive=True,
                    limit=settings.page_size_max,
                    offset=0,
                ).items
            },
            "seat_deactivate_operation_ids": {
                seat.id: str(uuid4())
                for seat in service.list_seats(
                    actor,
                    item.id,
                    include_inactive=True,
                    limit=settings.page_size_max,
                    offset=0,
                ).items
            },
        }
        for item in page.items
    ]
    return templates.TemplateResponse(
        request=request,
        name="admin/classrooms/index.html",
        context=_page_context(
            request,
            actor,
            entries=entries,
            create_operation_id=str(uuid4()),
            error=error,
        ),
        status_code=status_code,
    )


def _render_alerts(
    request: Request,
    *,
    actor: User,
    service: ClassroomService,
    settings: Settings,
    alert_status: AfterHoursAlertStatus | None = None,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
):
    page = service.list_alerts(
        actor,
        status=alert_status,
        classroom_id=None,
        business_date=None,
        limit=settings.page_size_max,
        offset=0,
    )
    classrooms = {
        item.id: item
        for item in service.list_classrooms(
            actor,
            include_inactive=True,
            limit=settings.page_size_max,
            offset=0,
        ).items
    }
    return templates.TemplateResponse(
        request=request,
        name="admin/alerts/index.html",
        context=_page_context(
            request,
            actor,
            page=page,
            classrooms=classrooms,
            selected_status=alert_status,
            resolve_operation_ids={item.id: str(uuid4()) for item in page.items},
            error=error,
        ),
        status_code=status_code,
    )


def _parse_schedule_lines(value: str) -> tuple[ClassroomSchedule, ...]:
    if not value.strip():
        return ()
    schedules: list[ClassroomSchedule] = []
    try:
        for line in value.splitlines():
            day, opens_at, closes_at = [part.strip() for part in line.split(",")]
            schedules.append(
                ClassroomSchedule(
                    day_of_week=int(day),
                    opens_at=time.fromisoformat(opens_at),
                    closes_at=time.fromisoformat(closes_at),
                )
            )
    except (TypeError, ValueError):
        raise ClassroomInputError(
            "일정은 day_of_week,HH:MM,HH:MM 형식으로 입력해 주세요."
        ) from None
    return tuple(schedules)


def _parse_seat_lines(value: str) -> tuple[SeatObservation, ...]:
    observations: list[SeatObservation] = []
    try:
        for line in value.splitlines():
            seat_id, occupied, confidence = [part.strip() for part in line.split(",")]
            normalized = occupied.lower()
            if normalized not in {"true", "false"}:
                raise ValueError
            observations.append(
                SeatObservation(
                    seat_id=seat_id,
                    occupied=normalized == "true",
                    confidence=float(confidence),
                )
            )
    except (TypeError, ValueError):
        raise ClassroomInputError(
            "좌석은 seat_id,true|false,confidence 형식으로 입력해 주세요."
        ) from None
    return tuple(observations)


def _form_geometry(form: SeatCreateForm) -> SeatGeometry | None:
    values = (form.x, form.y, form.width, form.height)
    if all(value is None or not value.strip() for value in values):
        return None
    if any(value is None or not value.strip() for value in values):
        raise ClassroomInputError("geometry 숫자 네 개를 모두 입력하거나 모두 비워 주세요.")
    try:
        return SeatGeometry(
            x=float(form.x),  # type: ignore[arg-type]
            y=float(form.y),  # type: ignore[arg-type]
            width=float(form.width),  # type: ignore[arg-type]
            height=float(form.height),  # type: ignore[arg-type]
        )
    except ValueError:
        raise ClassroomInputError("geometry는 숫자로 입력해 주세요.") from None


def _require_page_seat_membership(
    service: ClassroomService,
    actor: User,
    *,
    classroom_id: str,
    seat_id: str,
    settings: Settings,
) -> None:
    page = service.list_seats(
        actor,
        classroom_id,
        include_inactive=True,
        limit=settings.page_size_max,
        offset=0,
    )
    if all(item.id != seat_id for item in page.items):
        raise ClassroomInputError("좌석이 요청한 강의실 소속이 아닙니다.")


def _page_context(request: Request, actor: User, **values: object) -> dict[str, object]:
    return {
        "current_user": actor,
        "csrf_token": request.cookies.get(CSRF_COOKIE, ""),
        "can_view_employees": True,
        "can_manage_users": actor.role in ADMIN_ROLES,
        "can_manage_employees": actor.role in ADMIN_ROLES,
        "show_employee_dev_tools": False,
        "show_notification_dev_tools": False,
        **values,
    }
