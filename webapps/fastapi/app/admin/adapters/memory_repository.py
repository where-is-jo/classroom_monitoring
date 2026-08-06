"""Read-only administrator projection over in-memory source repositories."""

from __future__ import annotations

from datetime import datetime

from ...audit.adapters.memory_repository import InMemoryAuditRepository
from ...classrooms.adapters.memory_repository import InMemoryClassroomRepository
from ...classrooms.models import AfterHoursAlertStatus, SeatOccupancy
from ...employees.adapters.memory_repository import InMemoryEmployeeRepository
from ...employees.models import EmployeeStatus
from ...interview_waits.adapters.memory_repository import (
    InMemoryInterviewWaitRepository,
)
from ...interview_waits.models import InterviewWaitStatus
from ...notifications.adapters.memory_repository import InMemoryNotificationRepository
from ...notifications.models import MockDeliveryStatus
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
    InterviewWaitSummary,
    NotificationSummary,
)


class InMemoryAdminDashboardRepository:
    def __init__(
        self,
        employees: InMemoryEmployeeRepository,
        interview_waits: InMemoryInterviewWaitRepository,
        classrooms: InMemoryClassroomRepository,
        notifications: InMemoryNotificationRepository,
        audit: InMemoryAuditRepository,
    ) -> None:
        self._employees = employees
        self._interview_waits = interview_waits
        self._classrooms = classrooms
        self._notifications = notifications
        self._audit = audit

    def get_snapshot(
        self,
        *,
        department: str | None,
        classroom_id: str | None,
        delivery_failure_since: datetime,
    ) -> DashboardSnapshot:
        employees, _ = self._employees.dashboard_snapshot()
        waits, _ = self._interview_waits.dashboard_snapshot()
        classrooms, seats, alerts = self._classrooms.dashboard_snapshot()
        notifications, deliveries = self._notifications.dashboard_snapshot()

        active_employees = [
            item
            for item in employees
            if item.is_active and (department is None or item.department == department)
        ]
        employee_ids = {item.id for item in active_employees}
        active_waits = [
            item
            for item in waits
            if item.status in {InterviewWaitStatus.WAITING, InterviewWaitStatus.READY}
            and (department is None or item.employee_id in employee_ids)
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
            interview_waits=InterviewWaitSummary(
                waiting=sum(item.status == InterviewWaitStatus.WAITING for item in active_waits),
                ready=sum(item.status == InterviewWaitStatus.READY for item in active_waits),
            ),
            classrooms=ClassroomSummary(
                active=len(active_classrooms),
                occupied_seats=sum(
                    item.current_occupancy.state == SeatOccupancy.OCCUPIED for item in active_seats
                ),
                unknown_seats=sum(
                    item.current_occupancy.state == SeatOccupancy.UNKNOWN for item in active_seats
                ),
            ),
            alerts=AlertSummary(open_after_hours=len(scoped_alerts)),
            notifications=NotificationSummary(
                unread=sum(not item.is_read for item in notifications),
                failed_mock_deliveries_24h=sum(
                    item.status
                    in {
                        MockDeliveryStatus.TEMPORARY_FAILURE,
                        MockDeliveryStatus.PERMANENT_FAILURE,
                    }
                    and item.attempted_at >= delivery_failure_since
                    for item in deliveries
                ),
            ),
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
        _, wait_history = self._interview_waits.dashboard_snapshot()
        _, _, alerts = self._classrooms.dashboard_snapshot()
        notifications, _ = self._notifications.dashboard_snapshot()
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
        if activity_type in (None, DashboardActivityType.INTERVIEW_WAIT):
            items.extend(
                DashboardActivity(
                    id=f"wait:{item.id}",
                    type=DashboardActivityType.INTERVIEW_WAIT,
                    occurred_at=item.occurred_at,
                    title="면담 대기 변경",
                    description=f"{item.from_status.value if item.from_status else '-'} → {item.to_status.value}",
                    resource_type="interview_wait",
                    resource_id=item.wait_id,
                    target_route=f"/my/interview-waits/{item.wait_id}",
                )
                for item in wait_history
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
                    target_route="/admin/alerts",
                )
                for item in alerts
            )
        if activity_type in (None, DashboardActivityType.NOTIFICATION):
            items.extend(
                DashboardActivity(
                    id=f"notification:{item.id}",
                    type=DashboardActivityType.NOTIFICATION,
                    occurred_at=item.created_at,
                    title=item.title,
                    description=f"알림 유형 {item.type}",
                    resource_type="notification",
                    resource_id=item.id,
                    target_route="/notifications",
                )
                for item in notifications
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
