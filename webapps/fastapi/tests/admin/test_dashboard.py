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
from app.auth.dependencies import require_admin, require_csrf, require_page_admin
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
from app.shared.dependencies import get_admin_dashboard_service, get_classroom_service
from app.shared.errors import RepositoryUnavailableError
from tests.helpers.interview_wait import InterviewWaitStack, build_interview_wait_stack


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
        classrooms,
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
    assert summary.classrooms.active == 1
    assert summary.classrooms.active_seats == 1
    assert summary.classrooms.occupied_seats == 1
    assert summary.alerts.open_after_hours == 1
    assert not hasattr(summary, "interview_waits")
    assert not hasattr(summary, "notifications")

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

    stack.waits.employees.create_employee()
    _create_classroom_fixture(stack)
    page = stack.service.list_activities(stack.waits.employees.admin, limit=50)
    assert page.total >= 3
    assert [(item.occurred_at, item.id) for item in page.items] == sorted(
        [(item.occurred_at, item.id) for item in page.items],
        key=lambda value: (-value[0].timestamp(), value[1]),
    )
    only_seats = stack.service.list_activities(
        stack.waits.employees.admin,
        activity_type=DashboardActivityType.SEAT_OCCUPANCY,
        limit=50,
    )
    assert only_seats.items
    assert all(item.type == DashboardActivityType.SEAT_OCCUPANCY for item in only_seats.items)
    assert {item.type for item in page.items} <= {
        DashboardActivityType.EMPLOYEE_STATUS,
        DashboardActivityType.SEAT_OCCUPANCY,
        DashboardActivityType.AFTER_HOURS_ALERT,
    }
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


def test_dashboard_lists_and_resolves_open_alert(
    dashboard_stack: DashboardStack,
) -> None:
    actor = dashboard_stack.waits.employees.admin
    classroom, _ = _create_classroom_fixture(dashboard_stack)
    alert = dashboard_stack.classrooms.list_alerts(
        status=None,
        classroom_id=classroom.id,
        business_date=None,
        limit=10,
        offset=0,
    ).items[0]
    app.dependency_overrides[require_page_admin] = lambda: actor
    app.dependency_overrides[require_csrf] = lambda: None
    app.dependency_overrides[get_admin_dashboard_service] = lambda: dashboard_stack.service
    app.dependency_overrides[get_classroom_service] = lambda: dashboard_stack.classroom_service
    try:
        with TestClient(app) as client:
            page = client.get("/admin")
            resolved = client.post(
                f"/admin/alerts/{alert.id}/resolve",
                data={
                    "expected_version": str(alert.version),
                    "operation_id": str(uuid4()),
                },
                follow_redirects=False,
            )
        assert page.status_code == 200
        assert "Dashboard Room" in page.text
        assert "최근 열린 마감 후 경고" in page.text
        assert resolved.status_code == 303
        assert resolved.headers["location"].endswith("/admin#open-alerts")
        assert dashboard_stack.service.get_summary(actor).alerts.open_after_hours == 0
    finally:
        app.dependency_overrides.clear()


def test_dashboard_api_page_and_full_503_policy(
    dashboard_stack: DashboardStack,
) -> None:
    actor = dashboard_stack.waits.employees.admin
    app.dependency_overrides[require_admin] = lambda: actor
    app.dependency_overrides[require_page_admin] = lambda: actor
    app.dependency_overrides[get_admin_dashboard_service] = lambda: dashboard_stack.service
    app.dependency_overrides[get_classroom_service] = lambda: dashboard_stack.classroom_service
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/admin/dashboard-summary")
            audit_api = client.get("/api/v1/admin/audit-logs")
            page = client.get("/admin")
            audit = client.get("/admin/audit-logs")
        assert response.status_code == page.status_code == 200
        assert audit_api.status_code == 200
        assert audit_api.headers["deprecation"] == "true"
        assert audit.status_code == 404
        assert response.json()["employees"]["total"] == 0
        assert "interview_waits" not in response.json()
        assert "notifications" not in response.json()
        assert "관리자 대시보드" in page.text
        assert "0" in page.text
        assert "활성 면담 대기" not in page.text
        assert "Mock 전달 실패" not in page.text
        assert "운영 보조 지표" not in page.text
        assert "읽지 않은 알림</dt>" not in page.text
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
