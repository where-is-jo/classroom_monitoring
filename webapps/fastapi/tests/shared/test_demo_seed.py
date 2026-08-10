"""memory demo seed의 최소 데이터와 반복 실행 멱등성을 검증한다."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from secrets import token_urlsafe

import pytest
from pytest import MonkeyPatch

from app.classrooms.models import AfterHoursAlertStatus, SeatOccupancy
from app.employees.models import EmployeeStatus
from app.interview_waits.models import InterviewWaitStatus
from app.shared import dependencies
from app.shared.config import Settings
from app.users.models import UserRole
from tests.helpers.settings import make_settings


@pytest.fixture(autouse=True)
def isolated_memory_repositories() -> Iterator[None]:
    _clear_memory_repositories()
    yield
    _clear_memory_repositories()


def test_memory_demo_seed는_전체_시연_data를_멱등하게_준비한다(
    monkeypatch: MonkeyPatch,
) -> None:
    settings = _demo_settings()
    monkeypatch.setattr(dependencies, "get_settings", lambda: settings)

    dependencies.initialize_data_store()
    dependencies.initialize_data_store()

    user_repository = dependencies.get_user_repository(settings)
    users = user_repository.list_users(
        limit=50,
        offset=0,
        role=None,
        status=None,
        search=None,
    )
    assert Counter(user.role for user in users.items) == Counter(
        {
            UserRole.STUDENT: 1,
            UserRole.STAFF: 2,
            UserRole.ADMIN: 1,
        }
    )
    assert all(user.email.endswith(".invalid") for user in users.items)
    student = next(user for user in users.items if user.role == UserRole.STUDENT)
    admin = next(user for user in users.items if user.role == UserRole.ADMIN)
    staff_users = sorted(
        (user for user in users.items if user.role == UserRole.STAFF),
        key=lambda user: user.email,
    )

    audit_service = dependencies.get_audit_service(dependencies.get_audit_repository(settings))
    notification_service = dependencies.get_notification_service(
        dependencies.get_notification_repository(settings),
        user_repository,
        settings,
    )
    employee_service = dependencies.get_employee_service(
        dependencies.get_employee_repository(settings),
        user_repository,
        audit_service,
        settings,
    )
    interview_service = dependencies.get_interview_wait_service(
        dependencies.get_interview_wait_repository(settings),
        dependencies.get_employee_repository(settings),
        user_repository,
        notification_service,
        settings,
    )
    classroom_service = dependencies.get_classroom_service(
        dependencies.get_classroom_repository(settings),
        user_repository,
        notification_service,
        audit_service,
        settings,
    )

    employees = employee_service.list_employees(admin, limit=50, offset=0)
    assert Counter(item.current_status.status for item in employees.items) == Counter(
        {
            EmployeeStatus.WORKING: 1,
            EmployeeStatus.ON_CALL: 1,
            EmployeeStatus.AWAY: 1,
            EmployeeStatus.OFFSITE: 1,
        }
    )
    waits = interview_service.list_requester_waits(
        student,
        status=None,
        limit=50,
        offset=0,
    )
    assert Counter(item.status for item in waits.items) == Counter(
        {
            InterviewWaitStatus.WAITING: 1,
            InterviewWaitStatus.READY: 1,
        }
    )

    classrooms = classroom_service.list_classrooms(
        admin,
        include_inactive=True,
        limit=50,
        offset=0,
    )
    assert {item.code for item in classrooms.items} == {"A101", "B203"}
    for classroom in classrooms.items:
        occupancy = classroom_service.occupancy_summary(admin, classroom.id)
        assert {seat.current_occupancy.state for seat in occupancy.seats} == {
            SeatOccupancy.VACANT,
            SeatOccupancy.OCCUPIED,
            SeatOccupancy.UNKNOWN,
        }
    alerts = classroom_service.list_alerts(
        admin,
        status=None,
        classroom_id=None,
        business_date=None,
        limit=50,
        offset=0,
    )
    assert Counter(item.status for item in alerts.items) == Counter(
        {
            AfterHoursAlertStatus.OPEN: 1,
            AfterHoursAlertStatus.RESOLVED: 1,
        }
    )

    assert notification_service.popover_unread_count(student) == 1
    assert [notification_service.popover_unread_count(user) for user in staff_users] == [1, 1]
    assert notification_service.popover_unread_count(admin) == 2


def _demo_settings() -> Settings:
    return make_settings(
        app_env="local",
        database_mode="memory",
        demo_mode_enabled=True,
        auth_seed_enabled=True,
        jwt_access_secret=_secret("access"),
        jwt_refresh_secret=_secret("refresh"),
        csrf_secret=_secret("csrf"),
        audit_ip_hash_secret=_secret("audit"),
        web_origin="http://127.0.0.1:8000",
        auth_seed_student_password=_password("student"),
        auth_seed_staff_password=_password("staff"),
        auth_seed_admin_password=_password("admin"),
    )


def _secret(label: str) -> str:
    return f"{label}-{token_urlsafe(32)}"


def _password(label: str) -> str:
    return f"Aa1!{label}-{token_urlsafe(24)}"


def _clear_memory_repositories() -> None:
    dependencies._admin_dashboard_repository.cache_clear()
    dependencies._classroom_repository.cache_clear()
    dependencies._interview_wait_repository.cache_clear()
    dependencies._notification_repository.cache_clear()
    dependencies._employee_repository.cache_clear()
    dependencies._audit_repository.cache_clear()
    dependencies._auth_repository.cache_clear()
    dependencies._user_repository.cache_clear()
    dependencies._event_repository.cache_clear()
