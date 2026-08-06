"""Administrator dashboard APIs and server-rendered pages."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from ..auth.dependencies import CSRF_COOKIE, require_admin, require_page_admin
from ..shared.config import Settings
from ..shared.dependencies import get_admin_dashboard_service, get_settings
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
) -> Response:
    summary = service.get_summary(actor, department=department, classroom_id=classroom_id)
    activities = service.list_activities(actor, limit=10, offset=0)
    return templates.TemplateResponse(
        request=request,
        name="admin/dashboard.html",
        context=_page_context(
            request,
            actor,
            summary=summary,
            activities=activities,
            department=department or "",
            classroom_id=classroom_id or "",
        ),
    )


@page_router.get("/audit-logs", response_class=Response, include_in_schema=False)
def audit_log_page(
    request: Request,
    actor_user_id: str | None = None,
    action: str | None = None,
    resource: str | None = None,
    from_time: Annotated[datetime | None, Query(alias="from")] = None,
    to_time: Annotated[datetime | None, Query(alias="to")] = None,
    limit: int | None = None,
    offset: int = 0,
    actor: User = Depends(require_page_admin),
    service: AdminDashboardService = Depends(get_admin_dashboard_service),
    settings: Settings = Depends(get_settings),
) -> Response:
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
    return templates.TemplateResponse(
        request=request,
        name="admin/audit_logs.html",
        context=_page_context(
            request,
            actor,
            page=page,
            limit=resolved_limit,
            offset=resolved_offset,
            actor_user_id=actor_user_id or "",
            action=action or "",
            resource=resource or "",
            from_time=from_time.isoformat() if from_time else "",
            to_time=to_time.isoformat() if to_time else "",
        ),
    )


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
