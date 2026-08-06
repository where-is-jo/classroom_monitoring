"""Administrator dashboard summaries, activities, audit, and route policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import NoReturn
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.admin.adapters.memory_repository import InMemoryAdminDashboardRepository
from app.admin.errors import AdminQueryInputError
from app.admin.models import AuditLogPage, DashboardActivityType
from app.admin.service import AdminDashboardService
from app.audit.models import AuditLog
from app.audit.service import AuditService
from app.auth.dependencies import require_admin, require_page_admin
from app.auth.errors import PermissionDeniedError
from app.classrooms.adapters.memory_repository import InMemoryClassroomRepository
from app.classrooms.models import (
    Classroom,
    ClassroomSchedule,
    CreateClassroomCommand,
    CreateSeatCommand,
    RecordSeatObservationBatchCommand,
    ReplaceSchedulesCommand,
    ResolveAfterHoursAlertCommand,
    Seat,
    SeatObservation,
)
from app.classrooms.service import ClassroomService
from app.main import app
from app.notifications.models import CreateNotificationCommand
from app.notifications.service import NotificationService
from app.shared.dependencies import get_admin_dashboard_service
from app.shared.errors import RepositoryUnavailableError
from tests.interview_wait_helpers import InterviewWaitStack, build_interview_wait_stack


@dataclass
class DashboardStack:
    waits: InterviewWaitStack
    classrooms: InMemoryClassroomRepository
    classroom_service: ClassroomService
    service: AdminDashboardService


@pytest.fixture
def dashboard_stack() -> DashboardStack:
    waits = build_interview_wait_stack()
    classrooms = InMemoryClassroomRepository()
    classroom_service = ClassroomService(
        classrooms,
        waits.employees.auth.users,
        waits.notification_service,
        AuditService(waits.employees.auth.audit, clock=waits.employees.auth.clock),
        occupancy_confidence_threshold=0.6,
        clock=waits.employees.auth.clock,
    )
    repository = InMemoryAdminDashboardRepository(
        waits.employees.employees,
        waits.waits,
        classrooms,
        waits.notifications,
        waits.employees.auth.audit,
    )
    return DashboardStack(
        waits=waits,
        classrooms=classrooms,
        classroom_service=classroom_service,
        service=AdminDashboardService(repository, clock=waits.employees.auth.clock),
    )


def _create_classroom_fixture(stack: DashboardStack) -> tuple[Classroom, Seat]:
    actor = stack.waits.employees.admin
    service = stack.classroom_service
    classroom = service.create_classroom(
        actor,
        CreateClassroomCommand(
            code="DASH-101",
            name="Dashboard Room",
            location="A",
            timezone="Asia/Seoul",
            after_hours_grace_minutes=0,
            operation_id=str(uuid4()),
        ),
        ip_fingerprint="test",
    )
    scheduled = service.replace_schedules(
        actor,
        ReplaceSchedulesCommand(
            classroom_id=classroom.id,
            schedules=(ClassroomSchedule(day_of_week=2, opens_at=time(9), closes_at=time(17)),),
            expected_version=classroom.version,
            operation_id=str(uuid4()),
        ),
        ip_fingerprint="test",
    )
    seat = service.create_seat(
        actor,
        CreateSeatCommand(
            classroom_id=classroom.id,
            code="A-1",
            label="Seat A-1",
            geometry=None,
            operation_id=str(uuid4()),
        ),
        ip_fingerprint="test",
    )
    result = service.record_mock_observation_batch(
        actor,
        RecordSeatObservationBatchCommand(
            event_id=str(uuid4()),
            classroom_id=classroom.id,
            observed_at=stack.waits.employees.auth.clock(),
            observations=(SeatObservation(seat_id=seat.id, occupied=True, confidence=0.9),),
        ),
    )
    assert result.alert_count == 1
    return scheduled, seat


def test_summary_excludes_inactive_sources_and_alert_resolution_decrements(
    dashboard_stack: DashboardStack,
) -> None:
    stack = dashboard_stack
    active = stack.waits.employees.create_employee(employee_no="ACTIVE-1")
    inactive = stack.waits.employees.create_employee(employee_no="INACTIVE-1")
    stack.waits.employees.service.deactivate_employee(
        stack.waits.employees.admin,
        inactive.id,
        expected_version=inactive.version,
        operation_id=str(uuid4()),
        ip_fingerprint="test",
    )
    stack.waits.create_wait(active.id)
    classroom, _ = _create_classroom_fixture(stack)

    inactive_room = stack.classroom_service.create_classroom(
        stack.waits.employees.admin,
        CreateClassroomCommand(
            code="INACTIVE-ROOM",
            name="Inactive",
            location="B",
            timezone="Asia/Seoul",
            after_hours_grace_minutes=0,
            operation_id=str(uuid4()),
        ),
        ip_fingerprint="test",
    )
    stack.classroom_service.deactivate_classroom(
        stack.waits.employees.admin,
        inactive_room.id,
        expected_version=inactive_room.version,
        operation_id=str(uuid4()),
        ip_fingerprint="test",
    )

    fail_service = NotificationService(
        stack.waits.notifications,
        stack.waits.employees.auth.users,
        clock=stack.waits.employees.auth.clock,
        mock_delivery_mode="always_fail",
    )
    fail_service.create(
        CreateNotificationCommand(
            recipient_user_id=stack.waits.employees.admin.id,
            type="DASHBOARD_TEST",
            title="실패 테스트",
            body="본문",
            data={},
            operation_id=str(uuid4()),
        )
    )

    summary = stack.service.get_summary(stack.waits.employees.admin)
    assert summary.employees.total == 1
    assert summary.interview_waits.waiting == 1
    assert summary.classrooms.active == 1
    assert summary.classrooms.occupied_seats == 1
    assert summary.alerts.open_after_hours == 1
    assert summary.notifications.failed_mock_deliveries_24h == 1
    assert summary.notifications.unread >= 1

    alert = stack.classrooms.list_alerts(
        status=None, classroom_id=classroom.id, business_date=None, limit=10, offset=0
    ).items[0]
    stack.classroom_service.resolve_alert(
        stack.waits.employees.admin,
        ResolveAfterHoursAlertCommand(
            alert_id=alert.id, expected_version=alert.version, operation_id=str(uuid4())
        ),
        ip_fingerprint="test",
    )
    assert stack.service.get_summary(stack.waits.employees.admin).alerts.open_after_hours == 0


def test_activity_order_filter_permissions_and_zero_state(
    dashboard_stack: DashboardStack,
) -> None:
    stack = dashboard_stack
    empty = stack.service.get_summary(stack.waits.employees.admin)
    assert empty.employees.total == empty.classrooms.active == 0
    with pytest.raises(PermissionDeniedError):
        stack.service.get_summary(stack.waits.employees.student)

    employee = stack.waits.employees.create_employee()
    stack.waits.create_wait(employee.id)
    fail_service = NotificationService(
        stack.waits.notifications,
        stack.waits.employees.auth.users,
        clock=stack.waits.employees.auth.clock,
        mock_delivery_mode=None,
    )
    fail_service.create(
        CreateNotificationCommand(
            recipient_user_id=stack.waits.employees.admin.id,
            type="ACTIVITY",
            title="활동",
            body="본문",
            data={},
            operation_id=str(uuid4()),
        )
    )
    page = stack.service.list_activities(stack.waits.employees.admin, limit=50)
    assert page.total >= 3
    assert [(item.occurred_at, item.id) for item in page.items] == sorted(
        [(item.occurred_at, item.id) for item in page.items],
        key=lambda value: (-value[0].timestamp(), value[1]),
    )
    only_notifications = stack.service.list_activities(
        stack.waits.employees.admin,
        activity_type=DashboardActivityType.NOTIFICATION,
        limit=50,
    )
    assert only_notifications.items
    assert all(item.type == DashboardActivityType.NOTIFICATION for item in only_notifications.items)
    with pytest.raises(AdminQueryInputError):
        stack.service.list_activities(
            stack.waits.employees.admin,
            from_time=stack.waits.employees.auth.clock(),
            to_time=stack.waits.employees.auth.clock(),
        )


def test_audit_filters_and_masks_sensitive_values(
    dashboard_stack: DashboardStack,
) -> None:
    stack = dashboard_stack
    clock = stack.waits.employees.auth.clock
    stack.waits.employees.auth.audit.append(
        AuditLog(
            id="audit-mask",
            operation_id="audit-mask-op",
            actor_user_id=stack.waits.employees.admin.id,
            action="UPDATED",
            resource_type="employee",
            resource_id="employee-1",
            before={"profile": {"password_hash": "secret"}},
            after={"access_token": "secret", "name": "safe"},
            ip_fingerprint=None,
            occurred_at=clock(),
        )
    )
    page = stack.service.list_audit_logs(
        stack.waits.employees.admin,
        action="UPDATED",
        resource="employee-1",
        limit=10,
    )
    assert page.total == 1
    assert page.items[0].before["profile"]["password_hash"] == "[masked]"
    assert page.items[0].after == {"access_token": "[masked]", "name": "safe"}


def test_dashboard_api_page_and_full_503_policy(
    dashboard_stack: DashboardStack,
) -> None:
    actor = dashboard_stack.waits.employees.admin
    app.dependency_overrides[require_admin] = lambda: actor
    app.dependency_overrides[require_page_admin] = lambda: actor
    app.dependency_overrides[get_admin_dashboard_service] = lambda: dashboard_stack.service
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/admin/dashboard-summary")
            page = client.get("/admin")
            audit = client.get("/admin/audit-logs")
        assert response.status_code == page.status_code == audit.status_code == 200
        assert response.json()["employees"]["total"] == 0
        assert "관리자 대시보드" in page.text
        assert "0" in page.text
    finally:
        app.dependency_overrides.clear()

    class FailingRepository:
        def get_snapshot(self, **_: object) -> NoReturn:
            raise RepositoryUnavailableError()

        def list_activities(self, **_: object) -> NoReturn:
            raise RepositoryUnavailableError()

        def list_audit_logs(self, **_: object) -> AuditLogPage:
            raise RepositoryUnavailableError()

    failing = AdminDashboardService(
        FailingRepository(), clock=dashboard_stack.waits.employees.auth.clock
    )
    app.dependency_overrides[require_admin] = lambda: actor
    app.dependency_overrides[get_admin_dashboard_service] = lambda: failing
    try:
        with TestClient(app) as client:
            failed = client.get("/api/v1/admin/dashboard-summary")
        assert failed.status_code == 503
        assert failed.json()["error"]["code"] == "REPOSITORY_UNAVAILABLE"
    finally:
        app.dependency_overrides.clear()
