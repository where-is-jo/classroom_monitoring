"""Main Specification v2 역할별 홈과 제품 탐색 계약."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.notifications.adapters.memory_repository import InMemoryNotificationRepository
from app.notifications.service import NotificationService
from app.shared.dependencies import (
    get_auth_service,
    get_notification_service,
    get_settings,
    get_user_service,
)
from app.users.models import UserRole
from tests.helpers.auth import AuthStack, build_auth_stack

PASSWORD = "ValidPassword1!"
ORIGIN = "http://testserver"


@pytest.fixture
def navigation_stack() -> AuthStack:
    return build_auth_stack()


@pytest.fixture
def navigation_client(navigation_stack: AuthStack) -> Iterator[TestClient]:
    notifications = NotificationService(
        InMemoryNotificationRepository(),
        navigation_stack.users,
        clock=navigation_stack.clock,
        mock_delivery_mode=None,
    )
    app.dependency_overrides[get_auth_service] = lambda: navigation_stack.auth_service
    app.dependency_overrides[get_user_service] = lambda: navigation_stack.user_service
    app.dependency_overrides[get_notification_service] = lambda: notifications
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("role", "expected_home"),
    [
        (UserRole.STUDENT, "/employees"),
        (UserRole.STAFF, "/staff/interview-waits"),
        (UserRole.ADMIN, "/admin"),
    ],
)
def test_login_후_역할별_기본_경로로_이동한다(
    navigation_client: TestClient,
    navigation_stack: AuthStack,
    role: UserRole,
    expected_home: str,
) -> None:
    user = navigation_stack.seed(role, email=f"{role.value.lower()}-home@example.invalid")

    response = navigation_client.post(
        "/login",
        headers={"Origin": ORIGIN},
        data={"email": user.email, "password": PASSWORD, "next": ""},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == expected_home


@pytest.mark.parametrize(
    ("role", "visible", "hidden"),
    [
        (
            UserRole.STUDENT,
            ("직원 찾기", "내 면담", "강의실 좌석"),
            ("접수된 면담", "실시간 모니터링", "사용자 관리"),
        ),
        (
            UserRole.STAFF,
            ("직원 조회", "접수된 면담", "강의실 좌석"),
            ("내 면담", "사용자 관리", "강의실 관리", "데모 모니터링"),
        ),
        (
            UserRole.ADMIN,
            ("대시보드", "사용자 관리", "직원 관리", "강의실 관리"),
            ("내 면담", "접수된 면담"),
        ),
    ],
)
def test_제품_탐색은_역할별_목표만_표시한다(
    navigation_client: TestClient,
    navigation_stack: AuthStack,
    role: UserRole,
    visible: tuple[str, ...],
    hidden: tuple[str, ...],
) -> None:
    user = navigation_stack.seed(role, email=f"{role.value.lower()}-nav@example.invalid")
    navigation_client.post(
        "/api/v1/auth/login",
        headers={"Origin": ORIGIN},
        json={"email": user.email, "password": PASSWORD},
    )

    page = navigation_client.get("/employees")

    assert page.status_code == 200
    for label in visible:
        assert label in page.text
    for label in hidden:
        assert label not in page.text
    for removed_label in (
        "탐지 이벤트",
        "감사 로그",
        "개발 도구",
        "Mock delivery",
        "마감 후 경고",
    ):
        assert removed_label not in page.text


def test_demo_mode에서_staff와_admin_영상_탐색을_표시한다(
    navigation_client: TestClient,
    navigation_stack: AuthStack,
) -> None:
    settings = get_settings().model_copy(update={"demo_mode_enabled": True})
    app.dependency_overrides[get_settings] = lambda: settings
    staff = navigation_stack.seed(UserRole.STAFF, email="staff-demo-nav@example.invalid")
    navigation_client.post(
        "/api/v1/auth/login",
        headers={"Origin": ORIGIN},
        json={"email": staff.email, "password": PASSWORD},
    )

    page = navigation_client.get("/employees")

    assert "데모 모니터링" in page.text
    assert "데모 영상 검색" in page.text


def test_외부_또는_역할에_허용되지_않은_return_to는_기본_홈으로_보낸다(
    navigation_client: TestClient,
    navigation_stack: AuthStack,
) -> None:
    student = navigation_stack.seed(UserRole.STUDENT, email="student-safe-return@example.invalid")

    for return_to in ("https://example.invalid", "//example.invalid", "/admin"):
        response = navigation_client.post(
            "/login",
            headers={"Origin": ORIGIN},
            data={"email": student.email, "password": PASSWORD, "next": return_to},
            follow_redirects=False,
        )
        assert response.headers["location"] == "/employees"
