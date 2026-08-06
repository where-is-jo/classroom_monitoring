"""사용자 API·Jinja2 화면과 관리자 사용자 여정 테스트."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import CSRF_COOKIE
from app.main import app
from app.shared.dependencies import get_auth_service, get_user_service
from app.users.models import UserRole
from tests.auth_helpers import AuthStack, build_auth_stack

ORIGIN = "http://testserver"
PASSWORD = "ValidPassword1!"


@pytest.fixture
def user_stack() -> AuthStack:
    return build_auth_stack()


@pytest.fixture
def user_client(user_stack: AuthStack):
    app.dependency_overrides[get_auth_service] = lambda: user_stack.auth_service
    app.dependency_overrides[get_user_service] = lambda: user_stack.user_service
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def login_admin(client: TestClient, stack: AuthStack) -> None:
    admin = stack.seed(UserRole.ADMIN, email="admin.journey@example.invalid")
    response = client.post(
        "/api/v1/auth/login",
        headers={"Origin": ORIGIN},
        json={"email": admin.email, "password": PASSWORD},
    )
    assert response.status_code == 200


def write_headers(client: TestClient) -> dict[str, str]:
    return {"Origin": ORIGIN, "X-CSRF-Token": client.cookies[CSRF_COOKIE]}


def test_관리자_로그인_생성_수정_비활성화_사용자_여정(
    user_client: TestClient,
    user_stack: AuthStack,
) -> None:
    login_admin(user_client, user_stack)

    created_response = user_client.post(
        "/api/v1/users",
        headers=write_headers(user_client),
        json={
            "email": " Journey.User@Example.Invalid ",
            "password": "JourneyPassword1!",
            "name": "가상 여정 사용자",
            "role": "STAFF",
            "operation_id": str(uuid4()),
        },
    )
    assert created_response.status_code == 201
    created = created_response.json()
    assert created["email"] == "journey.user@example.invalid"
    assert created_response.headers["location"].endswith(created["id"])
    assert "password" not in created_response.text

    updated_response = user_client.patch(
        f"/api/v1/users/{created['id']}",
        headers=write_headers(user_client),
        json={
            "expected_version": created["version"],
            "operation_id": str(uuid4()),
            "name": "수정된 가상 사용자",
            "role": "ADMIN",
        },
    )
    assert updated_response.status_code == 200
    assert updated_response.json()["name"] == "수정된 가상 사용자"
    assert updated_response.json()["role"] == "ADMIN"

    deactivated = user_client.request(
        "DELETE",
        f"/api/v1/users/{created['id']}",
        headers=write_headers(user_client),
        json={"operation_id": str(uuid4())},
    )
    assert deactivated.status_code == 204
    detail = user_client.get(f"/api/v1/users/{created['id']}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "INACTIVE"

    logs = user_stack.audit.list_all()
    assert {log.action for log in logs} >= {
        "USER_CREATED",
        "USER_UPDATED",
        "USER_DEACTIVATED",
    }
    assert "JourneyPassword1!" not in repr(logs)


def test_목록_API는_filter와_표준_pagination_응답을_사용한다(
    user_client: TestClient,
    user_stack: AuthStack,
) -> None:
    login_admin(user_client, user_stack)
    user_stack.seed(UserRole.STAFF, email="staff.search@example.invalid", name="찾을 직원")
    user_stack.seed(UserRole.STUDENT, email="student@example.invalid", name="가상 학생")

    response = user_client.get(
        "/api/v1/users?role=STAFF&status=ACTIVE&search=staff&limit=1&offset=0"
    )

    assert response.status_code == 200
    assert set(response.json()) == {"items", "total", "limit", "offset"}
    assert response.json()["limit"] == 1
    assert response.json()["offset"] == 0
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["role"] == "STAFF"


def test_사용자_API는_validation_CSRF와_CAS_오류_형식을_지킨다(
    user_client: TestClient,
    user_stack: AuthStack,
) -> None:
    login_admin(user_client, user_stack)
    no_csrf = user_client.post(
        "/api/v1/users",
        headers={"Origin": ORIGIN},
        json={
            "email": "new@example.invalid",
            "password": "NewUserPassword1!",
            "name": "가상 사용자",
            "role": "STAFF",
        },
    )
    invalid = user_client.post(
        "/api/v1/users",
        headers=write_headers(user_client),
        json={"email": "not-email", "password": "weak", "name": " ", "role": "STAFF"},
    )
    assert no_csrf.status_code == 403
    assert no_csrf.json()["error"]["code"] == "CSRF_VALIDATION_FAILED"
    assert invalid.status_code == 400
    assert set(invalid.json()["error"]) == {"code", "message", "details"}
    assert "weak" not in invalid.text


def test_사용자_화면은_정상_빈_오류_상태와_관리_action을_표시한다(
    user_client: TestClient,
    user_stack: AuthStack,
) -> None:
    login_admin(user_client, user_stack)
    page = user_client.get("/admin/users")
    empty = user_client.get("/admin/users?search=no-matching-virtual-user")

    assert page.status_code == 200
    assert "사용자 생성" in page.text
    assert "수정 저장" in page.text
    assert "비활성화" in page.text
    assert empty.status_code == 200
    assert "조건에 맞는 사용자가 없습니다" in empty.text

    duplicate = user_client.post(
        "/admin/users",
        headers={"Origin": ORIGIN},
        data={
            "csrf_token": user_client.cookies[CSRF_COOKIE],
            "operation_id": str(uuid4()),
            "email": "admin.journey@example.invalid",
            "password": "AnotherPassword1!",
            "name": "중복 가상 사용자",
            "role": "STAFF",
        },
    )
    assert duplicate.status_code == 409
    assert "이미 사용 중인 이메일" in duplicate.text


def test_본인_비밀번호_변경_API는_refresh를_폐기하고_민감정보를_숨긴다(
    user_client: TestClient,
    user_stack: AuthStack,
) -> None:
    login_admin(user_client, user_stack)
    response = user_client.patch(
        "/api/v1/auth/me/password",
        headers=write_headers(user_client),
        json={
            "current_password": PASSWORD,
            "new_password": "ChangedPassword2!",
            "operation_id": str(uuid4()),
        },
    )

    assert response.status_code == 200
    assert "password" not in response.text
    refresh = user_client.post(
        "/api/v1/auth/refresh",
        headers=write_headers(user_client),
    )
    assert refresh.status_code == 401


def test_OpenAPI는_auth_user_계약과_공통_오류_model을_노출한다(
    user_client: TestClient,
) -> None:
    schema = user_client.get("/openapi.json").json()
    expected_paths = {
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
        "/api/v1/auth/me",
        "/api/v1/auth/me/password",
        "/api/v1/users",
        "/api/v1/users/{user_id}",
    }

    assert expected_paths <= set(schema["paths"])
    login_responses = schema["paths"]["/api/v1/auth/login"]["post"]["responses"]
    assert login_responses["401"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }
    user_properties = schema["components"]["schemas"]["UserResponse"]["properties"]
    assert "password_hash" not in user_properties
    assert "access_token" not in user_properties
