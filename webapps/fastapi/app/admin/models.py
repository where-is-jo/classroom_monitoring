"""Administrator dashboard read models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class DashboardActivityType(StrEnum):
    EMPLOYEE_STATUS = "EMPLOYEE_STATUS"
    SEAT_OCCUPANCY = "SEAT_OCCUPANCY"
    AFTER_HOURS_ALERT = "AFTER_HOURS_ALERT"


@dataclass(frozen=True)
class EmployeeSummary:
    total: int
    working: int
    on_call: int
    away: int
    offsite: int


@dataclass(frozen=True)
class ClassroomSummary:
    active: int
    active_seats: int
    occupied_seats: int
    unknown_seats: int


@dataclass(frozen=True)
class AlertSummary:
    open_after_hours: int


@dataclass(frozen=True)
class DashboardSnapshot:
    employees: EmployeeSummary
    classrooms: ClassroomSummary
    alerts: AlertSummary


@dataclass(frozen=True)
class DashboardSummary:
    generated_at: datetime
    employees: EmployeeSummary
    classrooms: ClassroomSummary
    alerts: AlertSummary


@dataclass(frozen=True)
class DashboardActivity:
    id: str
    type: DashboardActivityType
    occurred_at: datetime
    title: str
    description: str
    resource_type: str
    resource_id: str
    target_route: str | None


@dataclass(frozen=True)
class DashboardActivityPage:
    items: list[DashboardActivity]
    total: int


@dataclass(frozen=True)
class AuditLogView:
    id: str
    actor_user_id: str | None
    action: str
    resource_type: str
    resource_id: str
    before: dict[str, Any]
    after: dict[str, Any]
    occurred_at: datetime


@dataclass(frozen=True)
class AuditLogPage:
    items: list[AuditLogView]
    total: int
