"""Classroom API, pages, mock registration, and user journey tests."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, time
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.dependencies import (
    CSRF_COOKIE,
    get_current_user,
    require_csrf,
)
from app.classrooms.models import (
    RecordSeatObservationBatchCommand,
    SeatObservation,
)
from app.main import app, include_classroom_routers
from app.shared.dependencies import (
    get_auth_service,
    get_classroom_service,
    get_notification_service,
    get_settings,
    get_user_service,
)
from app.users.models import User, UserRole
from tests.helpers.classroom import ClassroomStack, build_classroom_stack
from tests.helpers.settings import make_settings

ORIGIN = "http://testserver"
PASSWORD = "ValidPassword1!"


@pytest.fixture
def classroom_stack() -> ClassroomStack:
    return build_classroom_stack()


@pytest.fixture
def classroom_client(classroom_stack: ClassroomStack) -> Iterator[TestClient]:
    app.dependency_overrides[get_auth_service] = lambda: classroom_stack.auth.auth_service
    app.dependency_overrides[get_user_service] = lambda: classroom_stack.auth.user_service
    app.dependency_overrides[get_notification_service] = lambda: (
        classroom_stack.notification_service
    )
    app.dependency_overrides[get_classroom_service] = lambda: classroom_stack.service
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _login(client: TestClient, user: User) -> None:
    client.cookies.clear()
    response = client.post(
        "/api/v1/auth/login",
        headers={"Origin": ORIGIN},
        json={"email": user.email, "password": PASSWORD},
    )
    assert response.status_code == 200


def _headers(client: TestClient) -> dict[str, str]:
    return {"Origin": ORIGIN, "X-CSRF-Token": client.cookies[CSRF_COOKIE]}


def test_admin_crud_student_read_and_write_denial(
    classroom_client: TestClient,
    classroom_stack: ClassroomStack,
) -> None:
    _login(classroom_client, classroom_stack.admin)
    created = classroom_client.post(
        "/api/v1/classrooms",
        headers=_headers(classroom_client),
        json={
            "code": "ROOM-API",
            "name": "API Classroom",
            "location": "Building B",
            "timezone": "Asia/Seoul",
            "after_hours_grace_minutes": 10,
            "operation_id": str(uuid4()),
        },
    )
    assert created.status_code == 201
    classroom_id = created.json()["id"]
    assert created.headers["location"] == f"/api/v1/classrooms/{classroom_id}"
    duplicate_page = classroom_client.post(
        "/admin/classrooms",
        headers={"Origin": ORIGIN},
        data={
            "csrf_token": classroom_client.cookies[CSRF_COOKIE],
            "code": "ROOM-API",
            "name": "Duplicate",
            "location": "Building B",
            "timezone": "Asia/Seoul",
            "after_hours_grace_minutes": "10",
            "operation_id": str(uuid4()),
        },
    )
    assert duplicate_page.status_code == 409
    assert "같은 코드를 사용하는 강의실" in duplicate_page.text

    schedules = classroom_client.put(
        f"/api/v1/classrooms/{classroom_id}/schedules",
        headers=_headers(classroom_client),
        json={
            "schedules": [{"day_of_week": 2, "opens_at": "09:00", "closes_at": "17:00"}],
            "expected_version": created.json()["version"],
            "operation_id": str(uuid4()),
        },
    )
    assert schedules.status_code == 200

    seat = classroom_client.post(
        f"/api/v1/classrooms/{classroom_id}/seats",
        headers=_headers(classroom_client),
        json={
            "code": "A-1",
            "label": "Front Seat",
            "geometry": {"x": 0.1, "y": 0.2, "width": 0.2, "height": 0.2},
            "operation_id": str(uuid4()),
        },
    )
    assert seat.status_code == 201
    assert seat.json()["current_occupancy"]["state"] == "UNKNOWN"

    page_seat = classroom_client.post(
        f"/admin/classrooms/{classroom_id}/seats",
        headers={"Origin": ORIGIN},
        data={
            "csrf_token": classroom_client.cookies[CSRF_COOKIE],
            "code": "A-2",
            "label": "No Geometry Seat",
            "x": "",
            "y": "",
            "width": "",
            "height": "",
            "operation_id": str(uuid4()),
        },
        follow_redirects=False,
    )
    assert page_seat.status_code == 303

    _login(classroom_client, classroom_stack.student)
    listed = classroom_client.get("/api/v1/classrooms?limit=1&offset=0")
    occupancy = classroom_client.get(f"/api/v1/classrooms/{classroom_id}/occupancy")
    denied = classroom_client.post(
        f"/api/v1/classrooms/{classroom_id}/seats",
        headers=_headers(classroom_client),
        json={
            "code": "A-2",
            "label": "Denied",
            "geometry": None,
            "operation_id": str(uuid4()),
        },
    )
    assert listed.status_code == 200 and listed.json()["total"] == 1
    assert occupancy.status_code == 200 and occupancy.json()["total"] == 2
    assert denied.status_code == 403


def test_admin_classroom_form_assigns_multiple_responsible_staff(
    classroom_client: TestClient,
    classroom_stack: ClassroomStack,
) -> None:
    first_staff = classroom_stack.auth.seed(UserRole.STAFF, email="form-staff-1@example.invalid")
    second_staff = classroom_stack.auth.seed(UserRole.STAFF, email="form-staff-2@example.invalid")
    _login(classroom_client, classroom_stack.admin)

    response = classroom_client.post(
        "/admin/classrooms",
        headers={"Origin": ORIGIN},
        data={
            "csrf_token": classroom_client.cookies[CSRF_COOKIE],
            "code": "ROOM-FORM-STAFF",
            "name": "담당자 폼 강의실",
            "location": "Building C",
            "timezone": "Asia/Seoul",
            "after_hours_grace_minutes": "10",
            "operation_id": str(uuid4()),
            "responsible_staff_user_ids": [first_staff.id, second_staff.id],
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    page = classroom_stack.repository.list_classrooms(include_inactive=True, limit=50, offset=0)
    created = next(item for item in page.items if item.code == "ROOM-FORM-STAFF")
    assert created.responsible_staff_user_ids == tuple(sorted((first_staff.id, second_staff.id)))

    rendered = classroom_client.get("/admin/classrooms")
    assert rendered.status_code == 200
    assert rendered.text.count('name="responsible_staff_user_ids" multiple size="1"') == 2
    assert "Ctrl 또는 Cmd를 누른 채 여러 명을 선택할 수 있습니다." not in rendered.text
    assert "선택하지 않으면 경고는 ADMIN에게만 전달됩니다." in rendered.text


def test_after_hours_http_journey_and_pages(
    classroom_client: TestClient,
    classroom_stack: ClassroomStack,
) -> None:
    classroom = classroom_stack.create_classroom(code="ROOM-JOURNEY", grace=10)
    from app.classrooms.models import ClassroomSchedule, ReplaceSchedulesCommand

    classroom = classroom_stack.service.replace_schedules(
        classroom_stack.admin,
        ReplaceSchedulesCommand(
            classroom_id=classroom.id,
            schedules=(
                ClassroomSchedule(
                    day_of_week=2,
                    opens_at=time(9),
                    closes_at=time(17),
                ),
            ),
            expected_version=classroom.version,
            operation_id=str(uuid4()),
        ),
        ip_fingerprint="test-ip",
    )
    seat = classroom_stack.create_seat(classroom.id)
    _login(classroom_client, classroom_stack.admin)

    result = classroom_stack.service.record_mock_observation_batch(
        classroom_stack.admin,
        RecordSeatObservationBatchCommand(
            event_id=str(uuid4()),
            classroom_id=classroom.id,
            observed_at=datetime(2026, 8, 5, 8, 11, tzinfo=UTC),
            observations=(SeatObservation(seat.id, True, 0.9),),
        ),
    )
    assert result.alert_count == 1

    alerts = classroom_client.get("/api/v1/after-hours-alerts?status=OPEN")
    notifications = classroom_client.get("/api/v1/notifications")
    public_page = classroom_client.get("/classrooms")
    detail_page = classroom_client.get(f"/classrooms/{classroom.id}")
    admin_page = classroom_client.get("/admin/classrooms")
    alert_page = classroom_client.get("/admin/alerts")
    assert alerts.status_code == 200 and alerts.json()["total"] == 1
    assert notifications.status_code == 200 and notifications.json()["total"] == 1
    assert notifications.json()["items"][0]["target_route"] == "/admin"
    assert "Classroom ROOM-JOURNEY" in public_page.text
    assert "OCCUPIED" in detail_page.text
    assert "좌석 생성" in admin_page.text
    assert alert_page.status_code == 404

    alert = alerts.json()["items"][0]
    resolved = classroom_client.patch(
        f"/api/v1/after-hours-alerts/{alert['id']}",
        headers=_headers(classroom_client),
        json={
            "status": "RESOLVED",
            "expected_version": alert["version"],
            "operation_id": str(uuid4()),
        },
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "RESOLVED"


def test_mock_router_is_environment_gated_and_accepts_only_structured_values(
    classroom_stack: ClassroomStack,
) -> None:
    production = make_settings(
        _env_file=None,
        app_env="prod",
        database_mode="mongodb",
        database_url="mongodb://example.invalid",
        database_name="smart_office",
        mock_inputs_enabled=False,
        web_origin=ORIGIN,
    )
    production_app = FastAPI()
    include_classroom_routers(production_app, production)
    assert "/api/v1/mock-seat-observations" not in production_app.openapi()["paths"]
    assert "/admin/dev-tools/seat-observations" not in production_app.openapi()["paths"]

    classroom = classroom_stack.create_classroom()
    seat = classroom_stack.create_seat(classroom.id)
    development = make_settings(
        _env_file=None,
        app_env="local",
        database_mode="memory",
        mock_inputs_enabled=True,
        web_origin=ORIGIN,
    )
    development_app = FastAPI()
    include_classroom_routers(development_app, development)
    development_app.dependency_overrides[get_current_user] = lambda: classroom_stack.admin
    development_app.dependency_overrides[require_csrf] = lambda: None
    development_app.dependency_overrides[get_classroom_service] = lambda: classroom_stack.service
    development_app.dependency_overrides[get_settings] = lambda: development
    with TestClient(development_app) as client:
        response = client.post(
            "/api/v1/mock-seat-observations",
            json={
                "event_id": str(uuid4()),
                "classroom_id": classroom.id,
                "observed_at": "2026-08-05T08:00:00Z",
                "seats": [{"seat_id": seat.id, "occupied": False, "confidence": 0.9}],
            },
        )
        forbidden_metadata = client.post(
            "/api/v1/mock-seat-observations",
            json={
                "event_id": str(uuid4()),
                "classroom_id": classroom.id,
                "observed_at": "2026-08-05T08:00:00Z",
                "camera_id": "forbidden",
                "seats": [{"seat_id": seat.id, "occupied": False, "confidence": 0.9}],
            },
        )
    assert response.status_code == 201
    assert response.json()["processed_count"] == 1
    assert forbidden_metadata.status_code == 422


def test_classroom_pages_render_empty_and_permission_states(
    classroom_client: TestClient,
    classroom_stack: ClassroomStack,
) -> None:
    _login(classroom_client, classroom_stack.student)
    empty = classroom_client.get("/classrooms")
    denied_classrooms = classroom_client.get("/admin/classrooms")
    denied_alerts = classroom_client.get("/admin/alerts")
    assert empty.status_code == 200
    assert "표시할 활성 강의실이 없습니다" in empty.text
    assert denied_classrooms.status_code == 403
    assert denied_alerts.status_code == 404

    _login(classroom_client, classroom_stack.admin)
    alerts = classroom_client.get("/admin/alerts")
    assert alerts.status_code == 404
