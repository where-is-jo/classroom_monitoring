"""Interview wait API, page, authorization, and user-journey tests."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import CSRF_COOKIE
from app.employees.models import RecordEmployeeObservationCommand
from app.main import app
from app.shared.dependencies import (
    get_auth_service,
    get_employee_interview_coordinator,
    get_employee_service,
    get_interview_wait_service,
    get_notification_service,
    get_user_service,
)
from app.users.models import UserRole
from tests.interview_wait_helpers import InterviewWaitStack, build_interview_wait_stack

ORIGIN = "http://testserver"
PASSWORD = "ValidPassword1!"


@pytest.fixture
def interview_stack() -> InterviewWaitStack:
    return build_interview_wait_stack()


@pytest.fixture
def interview_client(interview_stack: InterviewWaitStack):
    dependencies = interview_stack.employees
    app.dependency_overrides[get_auth_service] = lambda: dependencies.auth.auth_service
    app.dependency_overrides[get_user_service] = lambda: dependencies.auth.user_service
    app.dependency_overrides[get_employee_service] = lambda: dependencies.service
    app.dependency_overrides[get_notification_service] = (
        lambda: interview_stack.notification_service
    )
    app.dependency_overrides[get_interview_wait_service] = lambda: interview_stack.service
    app.dependency_overrides[get_employee_interview_coordinator] = (
        lambda: interview_stack.coordinator
    )
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _login(client: TestClient, user) -> None:
    client.cookies.clear()
    response = client.post(
        "/api/v1/auth/login",
        headers={"Origin": ORIGIN},
        json={"email": user.email, "password": PASSWORD},
    )
    assert response.status_code == 200


def _csrf_headers(client: TestClient) -> dict[str, str]:
    return {"Origin": ORIGIN, "X-CSRF-Token": client.cookies[CSRF_COOKIE]}


def _create_via_api(client: TestClient, employee_id: str, *, message: str = "hello"):
    return client.post(
        "/api/v1/interview-waits",
        headers=_csrf_headers(client),
        json={
            "employee_id": employee_id,
            "message": message,
            "operation_id": str(uuid4()),
        },
    )


def test_api_scopes_lists_and_hides_another_requesters_wait(
    interview_client: TestClient,
    interview_stack: InterviewWaitStack,
) -> None:
    employee = interview_stack.employees.create_employee(
        user_id=interview_stack.employees.staff.id
    )
    other_student = interview_stack.employees.auth.seed(
        UserRole.STUDENT, email="other-student@example.invalid"
    )
    _login(interview_client, interview_stack.employees.student)
    created = _create_via_api(interview_client, employee.id)
    assert created.status_code == 201
    assert created.headers["location"].startswith("/api/v1/interview-waits/")
    wait_id = created.json()["id"]

    own = interview_client.get("/api/v1/interview-waits?limit=1&offset=0")
    assert own.status_code == 200
    assert own.json()["total"] == 1
    assert own.json()["items"][0]["id"] == wait_id

    _login(interview_client, other_student)
    assert interview_client.get("/api/v1/interview-waits").json()["total"] == 0
    hidden = interview_client.get(f"/api/v1/interview-waits/{wait_id}")
    assert hidden.status_code == 403

    _login(interview_client, interview_stack.employees.staff)
    staff_list = interview_client.get("/api/v1/interview-waits")
    assert staff_list.status_code == 200
    assert staff_list.json()["total"] == 1

    _login(interview_client, interview_stack.employees.admin)
    admin_list = interview_client.get("/api/v1/interview-waits")
    assert admin_list.status_code == 200
    assert admin_list.json()["total"] == 1


def test_return_notification_read_and_staff_complete_user_journey(
    interview_client: TestClient,
    interview_stack: InterviewWaitStack,
) -> None:
    employee = interview_stack.employees.create_employee(
        user_id=interview_stack.employees.staff.id,
        display_name="Target Employee",
    )
    student = interview_stack.employees.student
    _login(interview_client, student)
    created = _create_via_api(interview_client, employee.id, message="Please meet me")
    assert created.status_code == 201
    assert created.json()["status"] == "WAITING"
    wait_id = created.json()["id"]

    interview_stack.coordinator.record_mock_observation(
        interview_stack.employees.admin,
        RecordEmployeeObservationCommand(
            event_id=str(uuid4()),
            employee_id=employee.id,
            person_present=True,
            phone_detected=False,
            confidence=0.99,
            observed_at=interview_stack.employees.auth.clock(),
        ),
    )

    ready = interview_client.get(f"/api/v1/interview-waits/{wait_id}")
    notifications = interview_client.get("/api/v1/notifications")
    assert ready.status_code == 200
    assert ready.json()["status"] == "READY"
    assert notifications.status_code == 200
    assert notifications.json()["total"] == 1
    notification = notifications.json()["items"][0]
    assert notification["target_route"] == f"/my/interview-waits/{wait_id}"
    assert interview_client.get("/api/v1/notifications/unread-count").json() == {
        "unread_count": 1
    }

    marked = interview_client.patch(
        f"/api/v1/notifications/{notification['id']}",
        headers=_csrf_headers(interview_client),
        json={"operation_id": str(uuid4())},
    )
    assert marked.status_code == 200
    assert marked.json()["is_read"] is True

    _login(interview_client, interview_stack.employees.staff)
    completed = interview_client.patch(
        f"/api/v1/interview-waits/{wait_id}",
        headers=_csrf_headers(interview_client),
        json={"status": "COMPLETED", "operation_id": str(uuid4())},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "COMPLETED"


def test_pages_render_empty_normal_duplicate_and_permission_states(
    interview_client: TestClient,
    interview_stack: InterviewWaitStack,
) -> None:
    employee = interview_stack.employees.create_employee(
        user_id=interview_stack.employees.staff.id,
        display_name="Page Target",
    )
    student = interview_stack.employees.student
    _login(interview_client, student)
    empty = interview_client.get("/my/interview-waits")
    assert empty.status_code == 200
    assert "표시할 면담 대기가 없습니다" in empty.text

    created = _create_via_api(interview_client, employee.id, message="Page message")
    wait_id = created.json()["id"]
    normal = interview_client.get("/my/interview-waits")
    detail = interview_client.get(f"/my/interview-waits/{wait_id}")
    assert normal.status_code == 200
    assert "Page Target" in normal.text
    assert "활성" in normal.text
    assert "대기 취소" in normal.text
    assert "Page message" in detail.text

    duplicate = interview_client.post(
        "/my/interview-waits",
        headers={"Origin": ORIGIN},
        data={
            "csrf_token": interview_client.cookies[CSRF_COOKIE],
            "employee_id": employee.id,
            "message": "duplicate",
            "operation_id": str(uuid4()),
        },
    )
    assert duplicate.status_code == 409

    _login(interview_client, student)
    forbidden_staff_page = interview_client.get("/staff/interview-waits")
    assert forbidden_staff_page.status_code == 403

    _login(interview_client, interview_stack.employees.staff)
    staff_page = interview_client.get("/staff/interview-waits")
    assert staff_page.status_code == 200
    assert student.name in staff_page.text


def test_expiration_is_explicit_admin_write_and_get_remains_read_only(
    interview_client: TestClient,
    interview_stack: InterviewWaitStack,
) -> None:
    employee = interview_stack.employees.create_employee()
    _login(interview_client, interview_stack.employees.student)
    created = _create_via_api(interview_client, employee.id)
    wait_id = created.json()["id"]
    interview_stack.employees.auth.clock.advance(hours=25)
    _login(interview_client, interview_stack.employees.student)

    listed = interview_client.get("/api/v1/interview-waits")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["status"] == "WAITING"

    denied = interview_client.post(
        "/api/v1/interview-wait-expirations",
        headers=_csrf_headers(interview_client),
        json={"operation_id": str(uuid4())},
    )
    assert denied.status_code == 403

    _login(interview_client, interview_stack.employees.admin)
    evaluated = interview_client.post(
        "/api/v1/interview-wait-expirations",
        headers=_csrf_headers(interview_client),
        json={"operation_id": str(uuid4())},
    )
    assert evaluated.status_code == 200
    assert evaluated.json()["expired_count"] == 1
    assert interview_client.get(f"/api/v1/interview-waits/{wait_id}").json()[
        "status"
    ] == "EXPIRED"
