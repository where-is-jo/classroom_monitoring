"""HTTP response schemas for administrator read models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .models import (
    AuditLogPage,
    DashboardActivityPage,
    DashboardActivityType,
    DashboardSummary,
)


class EmployeeSummaryResponse(BaseModel):
    total: int = Field(ge=0)
    working: int = Field(ge=0)
    on_call: int = Field(ge=0)
    away: int = Field(ge=0)
    offsite: int = Field(ge=0)


class ClassroomSummaryResponse(BaseModel):
    active: int = Field(ge=0)
    active_seats: int = Field(ge=0)
    occupied_seats: int = Field(ge=0)
    unknown_seats: int = Field(ge=0)


class AlertSummaryResponse(BaseModel):
    open_after_hours: int = Field(ge=0)


class DashboardSummaryResponse(BaseModel):
    generated_at: datetime
    employees: EmployeeSummaryResponse
    classrooms: ClassroomSummaryResponse
    alerts: AlertSummaryResponse

    @classmethod
    def from_domain(cls, item: DashboardSummary) -> DashboardSummaryResponse:
        return cls(
            generated_at=item.generated_at,
            employees=EmployeeSummaryResponse(**item.employees.__dict__),
            classrooms=ClassroomSummaryResponse(**item.classrooms.__dict__),
            alerts=AlertSummaryResponse(**item.alerts.__dict__),
        )


class DashboardActivityResponse(BaseModel):
    id: str
    type: DashboardActivityType
    occurred_at: datetime
    title: str
    description: str
    resource_type: str
    resource_id: str
    target_route: str | None


class DashboardActivityListResponse(BaseModel):
    items: list[DashboardActivityResponse]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)

    @classmethod
    def from_page(
        cls, page: DashboardActivityPage, *, limit: int, offset: int
    ) -> DashboardActivityListResponse:
        return cls(
            items=[DashboardActivityResponse(**item.__dict__) for item in page.items],
            total=page.total,
            limit=limit,
            offset=offset,
        )


class AuditLogResponse(BaseModel):
    id: str
    actor_user_id: str | None
    action: str
    resource_type: str
    resource_id: str
    before: dict[str, Any]
    after: dict[str, Any]
    occurred_at: datetime


class AuditLogListResponse(BaseModel):
    items: list[AuditLogResponse]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)

    @classmethod
    def from_page(cls, page: AuditLogPage, *, limit: int, offset: int) -> AuditLogListResponse:
        return cls(
            items=[AuditLogResponse(**item.__dict__) for item in page.items],
            total=page.total,
            limit=limit,
            offset=offset,
        )
