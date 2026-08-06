"""Administrator dashboard APIs and server-rendered pages."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, Query, Request, status
from fastapi.responses import RedirectResponse, Response

from ..auth.dependencies import (
    CSRF_COOKIE,
    request_ip_fingerprint,
    require_admin,
    require_csrf,
    require_page_admin,
)
from ..classrooms.models import AfterHoursAlertStatus, ResolveAfterHoursAlertCommand
from ..classrooms.schemas import AlertResolveForm
from ..classrooms.service import ClassroomService
from ..shared.config import Settings
from ..shared.dependencies import (
    get_admin_dashboard_service,
    get_classroom_service,
    get_settings,
)
from ..shared.templating import templates
from ..users.models import User
from .models import DashboardActivityType
from .schemas import (
    AuditLogListResponse,
    DashboardActivityListResponse,
    DashboardSummaryResponse,
)
from .service import AdminDashboardService

api_router = APIRouter(prefix="/api/v1/admin", tags=["admin-dashboard"])
page_router = APIRouter(prefix="/admin", tags=["admin-dashboard-pages"])


def _paging(limit: int | None, offset: int, settings: Settings) -> tuple[int, int]:
    resolved = settings.page_size_default if limit is None else limit
    return max(1, min(resolved, settings.page_size_max)), max(0, offset)


@api_router.get(
    "/dashboard-summary",
    response_model=DashboardSummaryResponse,
    summary="관리자 대시보드 요약 조회",
    description="기존 원본 컬렉션을 읽기 전용으로 집계하며 원본 상태를 변경하지 않습니다.",
)
def dashboard_summary(
    department: Annotated[str | None, Query(min_length=1, max_length=120)] = None,
    classroom_id: Annotated[str | None, Query(min_length=1, max_length=120)] = None,
    actor: User = Depends(require_admin),
    service: AdminDashboardService = Depends(get_admin_dashboard_service),
) -> DashboardSummaryResponse:
    return DashboardSummaryResponse.from_domain(
        service.get_summary(actor, department=department, classroom_id=classroom_id)
    )


@api_router.get(
    "/dashboard-activities",
    response_model=DashboardActivityListResponse,
    summary="최근 관리자 활동 조회",
    description="기본 최근 24시간 활동을 시각 내림차순과 안정적인 ID 순서로 반환합니다.",
)
def dashboard_activities(
    activity_type: Annotated[DashboardActivityType | None, Query(alias="type")] = None,
    from_time: Annotated[datetime | None, Query(alias="from")] = None,
    to_time: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    actor: User = Depends(require_admin),
    service: AdminDashboardService = Depends(get_admin_dashboard_service),
    settings: Settings = Depends(get_settings),
) -> DashboardActivityListResponse:
    resolved_limit, resolved_offset = _paging(limit, offset, settings)
    page = service.list_activities(
        actor,
        activity_type=activity_type,
        from_time=from_time,
        to_time=to_time,
        limit=resolved_limit,
        offset=resolved_offset,
    )
    return DashboardActivityListResponse.from_page(
        page, limit=resolved_limit, offset=resolved_offset
    )


@api_router.get(
    "/audit-logs",
    response_model=AuditLogListResponse,
    summary="감사 로그 조회",
    description="민감 필드를 마스킹한 감사 로그를 필터와 페이지 단위로 반환합니다.",
)
def audit_logs(
    actor_user_id: Annotated[str | None, Query(min_length=1, max_length=120)] = None,
    action: Annotated[str | None, Query(min_length=1, max_length=120)] = None,
    resource: Annotated[str | None, Query(min_length=1, max_length=120)] = None,
    from_time: Annotated[datetime | None, Query(alias="from")] = None,
    to_time: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    actor: User = Depends(require_admin),
    service: AdminDashboardService = Depends(get_admin_dashboard_service),
    settings: Settings = Depends(get_settings),
) -> AuditLogListResponse:
    resolved_limit, resolved_offset = _paging(limit, offset, settings)
    page = service.list_audit_logs(
        actor,
        actor_user_id=actor_user_id,
        action=action,
        resource=resource,
        from_time=from_time,
        to_time=to_time,
        limit=resolved_limit,
        offset=resolved_offset,
    )
    return AuditLogListResponse.from_page(page, limit=resolved_limit, offset=resolved_offset)


@page_router.get("", response_class=Response, include_in_schema=False)
def dashboard_page(
    request: Request,
    department: str | None = None,
    classroom_id: str | None = None,
    actor: User = Depends(require_page_admin),
    service: AdminDashboardService = Depends(get_admin_dashboard_service),
    classroom_service: ClassroomService = Depends(get_classroom_service),
) -> Response:
    summary = service.get_summary(actor, department=department, classroom_id=classroom_id)
    activities = service.list_activities(actor, limit=10, offset=0)
    alerts = classroom_service.list_alerts(
        actor,
        status=AfterHoursAlertStatus.OPEN,
        classroom_id=classroom_id,
        business_date=None,
        limit=10,
        offset=0,
    )
    alert_entries = []
    for alert in alerts.items:
        classroom = classroom_service.get_classroom(actor, alert.classroom_id)
        seats = classroom_service.list_seats(
            actor,
            alert.classroom_id,
            include_inactive=True,
            limit=200,
            offset=0,
        )
        seat = next((item for item in seats.items if item.id == alert.seat_id), None)
        alert_entries.append(
            {
                "alert": alert,
                "classroom": classroom,
                "seat": seat,
                "operation_id": str(uuid4()),
            }
        )
    return templates.TemplateResponse(
        request=request,
        name="admin/dashboard.html",
        context=_page_context(
            request,
            actor,
            summary=summary,
            activities=activities,
            alert_entries=alert_entries,
            alert_total=alerts.total,
            department=department or "",
            classroom_id=classroom_id or "",
        ),
    )


@page_router.post("/alerts/{alert_id}/resolve", include_in_schema=False)
def resolve_dashboard_alert(
    alert_id: str,
    form: Annotated[AlertResolveForm, Form()],
    department: str | None = None,
    classroom_id: str | None = None,
    _: None = Depends(require_csrf),
    actor: User = Depends(require_page_admin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    classroom_service: ClassroomService = Depends(get_classroom_service),
) -> RedirectResponse:
    classroom_service.resolve_alert(
        actor,
        ResolveAfterHoursAlertCommand(
            alert_id=alert_id,
            expected_version=form.expected_version,
            operation_id=str(form.operation_id),
        ),
        ip_fingerprint=ip_fingerprint,
    )
    filters = {
        key: value
        for key, value in (("department", department), ("classroom_id", classroom_id))
        if value
    }
    target = "/admin" + (f"?{urlencode(filters)}" if filters else "") + "#open-alerts"
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


def _page_context(request: Request, actor: User, **values: object) -> dict[str, object]:
    return {
        "current_user": actor,
        "csrf_token": request.cookies.get(CSRF_COOKIE, ""),
        "can_view_employees": True,
        "can_manage_users": True,
        "can_manage_employees": True,
        "show_employee_dev_tools": False,
        "show_notification_dev_tools": False,
        **values,
    }
