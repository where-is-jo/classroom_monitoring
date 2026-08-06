"""Read-only administrator projection over in-memory source repositories."""

from __future__ import annotations

from datetime import datetime

from ...audit.adapters.memory_repository import InMemoryAuditRepository
from ...classrooms.adapters.memory_repository import InMemoryClassroomRepository
from ...classrooms.models import AfterHoursAlertStatus, SeatOccupancy
from ...employees.adapters.memory_repository import InMemoryEmployeeRepository
from ...employees.models import EmployeeStatus
from ..models import (
    AlertSummary,
    AuditLogPage,
    AuditLogView,
    ClassroomSummary,
    DashboardActivity,
    DashboardActivityPage,
    DashboardActivityType,
    DashboardSnapshot,
    EmployeeSummary,
)


class InMemoryAdminDashboardRepository:
    def __init__(
        self,
        employees: InMemoryEmployeeRepository,
        classrooms: InMemoryClassroomRepository,
        audit: InMemoryAuditRepository,
    ) -> None:
        self._employees = employees
        self._classrooms = classrooms
        self._audit = audit

    def get_snapshot(
        self,
        *,
        department: str | None,
        classroom_id: str | None,
    ) -> DashboardSnapshot:
        employees, _ = self._employees.dashboard_snapshot()
        classrooms, seats, _, alerts = self._classrooms.dashboard_snapshot()

        active_employees = [
            item
            for item in employees
            if item.is_active and (department is None or item.department == department)
        ]
        active_classrooms = [
            item
            for item in classrooms
            if item.is_active and (classroom_id is None or item.id == classroom_id)
        ]
        classroom_ids = {item.id for item in active_classrooms}
        active_seats = [
            item for item in seats if item.is_active and item.classroom_id in classroom_ids
        ]
        scoped_alerts = [
            item
            for item in alerts
            if item.status == AfterHoursAlertStatus.OPEN and item.classroom_id in classroom_ids
        ]

        counts = dict.fromkeys(EmployeeStatus, 0)
        for employee in active_employees:
            counts[employee.current_status.status] += 1
        return DashboardSnapshot(
            employees=EmployeeSummary(
                total=len(active_employees),
                working=counts[EmployeeStatus.WORKING],
                on_call=counts[EmployeeStatus.ON_CALL],
                away=counts[EmployeeStatus.AWAY],
                offsite=counts[EmployeeStatus.OFFSITE],
            ),
            classrooms=ClassroomSummary(
                active=len(active_classrooms),
                active_seats=len(active_seats),
                occupied_seats=sum(
                    item.current_occupancy.state == SeatOccupancy.OCCUPIED for item in active_seats
                ),
                unknown_seats=sum(
                    item.current_occupancy.state == SeatOccupancy.UNKNOWN for item in active_seats
                ),
            ),
            alerts=AlertSummary(open_after_hours=len(scoped_alerts)),
        )

    def list_activities(
        self,
        *,
        activity_type: DashboardActivityType | None,
        from_time: datetime,
        to_time: datetime,
        limit: int,
        offset: int,
    ) -> DashboardActivityPage:
        _, employee_history = self._employees.dashboard_snapshot()
        _, _, seat_history, alerts = self._classrooms.dashboard_snapshot()
        items: list[DashboardActivity] = []

        if activity_type in (None, DashboardActivityType.EMPLOYEE_STATUS):
            items.extend(
                DashboardActivity(
                    id=f"employee:{item.id}",
                    type=DashboardActivityType.EMPLOYEE_STATUS,
                    occurred_at=item.occurred_at,
                    title="직원 상태 변경",
                    description=f"{item.from_status.value if item.from_status else '-'} → {item.to_status.value}",
                    resource_type="employee",
                    resource_id=item.employee_id,
                    target_route=f"/employees/{item.employee_id}",
                )
                for item in employee_history
            )
        if activity_type in (None, DashboardActivityType.SEAT_OCCUPANCY):
            items.extend(
                DashboardActivity(
                    id=f"seat:{item.id}",
                    type=DashboardActivityType.SEAT_OCCUPANCY,
                    occurred_at=item.observed_at,
                    title="좌석 상태 변경",
                    description=f"{item.from_state.value} → {item.to_state.value}",
                    resource_type="seat",
                    resource_id=item.seat_id,
                    target_route=f"/classrooms/{item.classroom_id}",
                )
                for item in seat_history
                if item.state_changed
            )
        if activity_type in (None, DashboardActivityType.AFTER_HOURS_ALERT):
            items.extend(
                DashboardActivity(
                    id=f"alert:{item.id}",
                    type=DashboardActivityType.AFTER_HOURS_ALERT,
                    occurred_at=item.detected_at,
                    title="마감 후 좌석 경고",
                    description=f"좌석 {item.seat_id} · {item.status.value}",
                    resource_type="after_hours_alert",
                    resource_id=item.id,
                    target_route="/admin#open-alerts",
                )
                for item in alerts
            )
        items = [item for item in items if from_time <= item.occurred_at < to_time]
        items.sort(key=lambda item: (-item.occurred_at.timestamp(), item.id))
        return DashboardActivityPage(items=items[offset : offset + limit], total=len(items))

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
    ) -> AuditLogPage:
        logs = self._audit.list_all()
        if actor_user_id is not None:
            logs = [item for item in logs if item.actor_user_id == actor_user_id]
        if action is not None:
            logs = [item for item in logs if item.action == action]
        if resource is not None:
            logs = [
                item
                for item in logs
                if item.resource_type == resource or item.resource_id == resource
            ]
        if from_time is not None:
            logs = [item for item in logs if item.occurred_at >= from_time]
        if to_time is not None:
            logs = [item for item in logs if item.occurred_at < to_time]
        logs.sort(key=lambda item: (-item.occurred_at.timestamp(), item.id))
        total = len(logs)
        return AuditLogPage(
            items=[
                AuditLogView(
                    id=item.id,
                    actor_user_id=item.actor_user_id,
                    action=item.action,
                    resource_type=item.resource_type,
                    resource_id=item.resource_id,
                    before=item.before,
                    after=item.after,
                    occurred_at=item.occurred_at,
                )
                for item in logs[offset : offset + limit]
            ],
            total=total,
        )
