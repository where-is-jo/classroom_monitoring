"""임시 비밀번호 강제 변경과 세션 폐기 계약."""

from __future__ import annotations

from collections.abc import Iterator
from typing import cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from app.auth.dependencies import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE
from app.main import app
from app.notifications.adapters.memory_repository import InMemoryNotificationRepository
from app.notifications.service import NotificationService
from app.shared.dependencies import (
    get_auth_service,
    get_notification_service,
    get_user_service,
)
from app.users.adapters.mongo_repository import MongoUserRepository
from app.users.errors import UnsupportedUserRoleError
from app.users.models import CreateUserCommand, UpdateUserCommand, User, UserRole
from tests.auth_helpers import AuthStack, build_auth_stack

ORIGIN = "http://testserver"
TEMPORARY_PASSWORD = "TemporaryPassword1!"
NEW_PASSWORD = "ChangedPassword2!"


@pytest.fixture
def password_stack() -> AuthStack:
    return build_auth_stack()


@pytest.fixture
def password_client(password_stack: AuthStack) -> Iterator[TestClient]:
    notifications = NotificationService(
        InMemoryNotificationRepository(),
        password_stack.users,
        clock=password_stack.clock,
        mock_delivery_mode=None,
    )
    app.dependency_overrides[get_auth_service] = lambda: password_stack.auth_service
    app.dependency_overrides[get_user_service] = lambda: password_stack.user_service
    app.dependency_overrides[get_notification_service] = lambda: notifications
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _temporary_student(stack: AuthStack) -> User:
    admin = stack.seed(UserRole.ADMIN, email="password-admin@example.invalid")
    return stack.user_service.create_user(
        admin,
        CreateUserCommand(
            email="temporary-student@example.invalid",
            password=TEMPORARY_PASSWORD,
            name="데모 학생 01",
            role=UserRole.STUDENT,
            operation_id=str(uuid4()),
        ),
        ip_fingerprint="test",
    )


def _login_temporary(client: TestClient, email: str, *, next_path: str = "/classrooms") -> Response:
    return cast(
        Response,
        client.post(
            "/login",
            headers={"Origin": ORIGIN},
            data={"email": email, "password": TEMPORARY_PASSWORD, "next": next_path},
            follow_redirects=False,
        ),
    )


def test_관리자_생성_계정은_임시_비밀번호_flag와_응답_필드를_가진다(
    password_stack: AuthStack,
) -> None:
    user = _temporary_student(password_stack)

    assert user.must_change_password is True
    assert user.password_changed_at is None


def test_강제_변경_사용자는_비밀번호_화면_외의_page와_API를_사용할_수_없다(
    password_client: TestClient,
    password_stack: AuthStack,
) -> None:
    user = _temporary_student(password_stack)
    login = _login_temporary(password_client, user.email)

    assert login.status_code == 303
    assert login.headers["location"] == "/account/password?next=%2Fclassrooms"
    page = password_client.get("/classrooms", follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"].startswith("/account/password?next=")
    denied = password_client.get("/api/v1/employees")
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "PASSWORD_CHANGE_REQUIRED"
    allowed = password_client.get("/account/password?next=/classrooms")
    assert allowed.status_code == 200
    assert "임시 비밀번호 변경" in allowed.text
    assert 'name="new_password_confirm"' in allowed.text


def test_비밀번호_확인_오류와_현재값_재사용을_명확히_거부한다(
    password_client: TestClient,
    password_stack: AuthStack,
) -> None:
    user = _temporary_student(password_stack)
    _login_temporary(password_client, user.email)
    csrf = password_client.cookies[CSRF_COOKIE]
    common = {
        "csrf_token": csrf,
        "current_password": TEMPORARY_PASSWORD,
        "next": "/classrooms",
    }

    mismatch = password_client.post(
        "/account/password",
        headers={"Origin": ORIGIN},
        data={
            **common,
            "new_password": NEW_PASSWORD,
            "new_password_confirm": "DifferentPassword3!",
            "operation_id": str(uuid4()),
        },
    )
    unchanged = password_client.post(
        "/account/password",
        headers={"Origin": ORIGIN},
        data={
            **common,
            "new_password": TEMPORARY_PASSWORD,
            "new_password_confirm": TEMPORARY_PASSWORD,
            "operation_id": str(uuid4()),
        },
    )

    assert mismatch.status_code == 400
    assert "확인이 일치하지 않습니다" in mismatch.text
    assert unchanged.status_code == 400
    assert "현재 비밀번호와 달라야" in unchanged.text


def test_변경_성공은_flag를_해제하고_refresh와_cookie를_폐기한뒤_재로그인한다(
    password_client: TestClient,
    password_stack: AuthStack,
) -> None:
    user = _temporary_student(password_stack)
    _login_temporary(password_client, user.email)
    csrf = password_client.cookies[CSRF_COOKIE]

    changed = password_client.post(
        "/account/password",
        headers={"Origin": ORIGIN},
        data={
            "csrf_token": csrf,
            "current_password": TEMPORARY_PASSWORD,
            "new_password": NEW_PASSWORD,
            "new_password_confirm": NEW_PASSWORD,
            "next": "/classrooms",
            "operation_id": str(uuid4()),
        },
        follow_redirects=False,
    )

    assert changed.status_code == 303
    assert changed.headers["location"] == "/login?next=%2Fclassrooms"
    assert ACCESS_COOKIE not in password_client.cookies
    assert REFRESH_COOKIE not in password_client.cookies
    assert CSRF_COOKIE not in password_client.cookies
    stored = password_stack.users.get_user(user.id)
    assert stored is not None
    assert stored.must_change_password is False
    assert stored.password_changed_at == password_stack.clock()

    relogin = password_client.post(
        "/login",
        headers={"Origin": ORIGIN},
        data={"email": user.email, "password": NEW_PASSWORD, "next": "/classrooms"},
        follow_redirects=False,
    )
    assert relogin.headers["location"] == "/classrooms"


def test_SYSTEM_OPERATOR는_신규_생성이나_새_역할_지정이_거부된다(
    password_stack: AuthStack,
) -> None:
    admin = password_stack.seed(UserRole.ADMIN, email="role-admin@example.invalid")
    student = password_stack.seed(UserRole.STUDENT, email="role-student@example.invalid")
    with pytest.raises(UnsupportedUserRoleError):
        password_stack.user_service.create_user(
            admin,
            CreateUserCommand(
                email="operator-new@example.invalid",
                password=TEMPORARY_PASSWORD,
                name="지원하지 않는 역할",
                role=UserRole.SYSTEM_OPERATOR,
                operation_id=str(uuid4()),
            ),
            ip_fingerprint="test",
        )
    with pytest.raises(UnsupportedUserRoleError):
        password_stack.user_service.update_user(
            admin,
            UpdateUserCommand(
                user_id=student.id,
                expected_version=student.version,
                operation_id=str(uuid4()),
                role=UserRole.SYSTEM_OPERATOR,
            ),
            ip_fingerprint="test",
        )


def test_기존_Mongo_문서는_강제변경_false로_안전하게_migration된다(
    password_stack: AuthStack,
) -> None:
    user = password_stack.seed(UserRole.STAFF, email="legacy-user@example.invalid")
    document = MongoUserRepository._to_document(user)
    document.pop("must_change_password")
    document.pop("password_changed_at")

    migrated = MongoUserRepository._to_domain(document)

    assert migrated.must_change_password is False
    assert migrated.password_changed_at is None
