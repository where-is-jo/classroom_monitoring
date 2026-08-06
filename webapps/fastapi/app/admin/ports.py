"""Read-only query port for administrator dashboard data."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .models import (
    AuditLogPage,
    DashboardActivityPage,
    DashboardActivityType,
    DashboardSnapshot,
)


class AdminDashboardRepository(Protocol):
    def get_snapshot(
        self,
        *,
        department: str | None,
        classroom_id: str | None,
    ) -> DashboardSnapshot: ...

    def list_activities(
        self,
        *,
        activity_type: DashboardActivityType | None,
        from_time: datetime,
        to_time: datetime,
        limit: int,
        offset: int,
    ) -> DashboardActivityPage: ...

    def list_audit_logs(
        self,
        *,
        actor_user_id: str | None,
        action: str | None,
        resource: str | None,
        from_time: datetime | None,
        to_time: datetime | None,
        limit: int,
        offset: int,
    ) -> AuditLogPage: ...
