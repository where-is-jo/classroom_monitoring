"""인증 API, cookie 보안과 로그인 화면 테스트."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE
from app.main import app
from app.shared.config import Settings
from app.shared.dependencies import get_auth_service, get_settings, get_user_service
from app.users.models import UserRole, UserStatus
from tests.auth_helpers import AuthStack, build_auth_stack

ORIGIN = "http://testserver"
PASSWORD = "ValidPassword1!"


@pytest.fixture
def auth_stack() -> AuthStack:
    return build_auth_stack(account_max_failures=2)


@pytest.fixture
def auth_client(auth_stack: AuthStack):
    app.dependency_overrides[get_auth_service] = lambda: auth_stack.auth_service
    app.dependency_overrides[get_user_service] = lambda: auth_stack.user_service
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def login(client: TestClient, email: str, password: str = PASSWORD):
    return client.post(
        "/api/v1/auth/login",
        headers={"Origin": ORIGIN},
        json={"email": email, "password": password},
    )


def csrf_headers(client: TestClient) -> dict[str, str]:
    return {"Origin": ORIGIN, "X-CSRF-Token": client.cookies[CSRF_COOKIE]}


def test_login_API는_cookie를_발급하고_me에서_민감정보를_제외한다(
    auth_client: TestClient,
    auth_stack: AuthStack,
) -> None:
    user = auth_stack.seed(UserRole.ADMIN)

    response = login(auth_client, user.email.upper())

    assert response.status_code == 200
    assert response.json()["user"]["email"] == user.email
    assert "password" not in response.text
    assert "token" not in response.text
    set_cookie = response.headers.get_list("set-cookie")
    assert any(f"{ACCESS_COOKIE}=" in value and "HttpOnly" in value for value in set_cookie)
    assert any(f"{REFRESH_COOKIE}=" in value and "HttpOnly" in value for value in set_cookie)
    assert all("SameSite=lax" in value for value in set_cookie)
    me = auth_client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert set(me.json()["user"]) == {
        "id",
        "email",
        "name",
        "role",
        "status",
        "locked_until",
        "last_login_at",
        "created_at",
        "updated_at",
        "version",
    }


def test_production_session_cookie에는_Secure가_적용된다(
    auth_client: TestClient,
    auth_stack: AuthStack,
) -> None:
    user = auth_stack.seed(UserRole.ADMIN)
    production_settings = Settings(
        _env_file=None,
        app_env="prod",
        database_mode="mongodb",
        database_url="mongodb://example.invalid",
        database_name="smart_office",
        web_origin=ORIGIN,
    )
    app.dependency_overrides[get_settings] = lambda: production_settings

    response = login(auth_client, user.email)

    assert response.status_code == 200
    session_cookies = [
        value
        for value in response.headers.get_list("set-cookie")
        if value.startswith((f"{ACCESS_COOKIE}=", f"{REFRESH_COOKIE}="))
    ]
    assert session_cookies
    assert all("Secure" in value for value in session_cookies)


def test_login은_허용_origin과_일반화된_credential_오류를_사용한다(
    auth_client: TestClient,
    auth_stack: AuthStack,
) -> None:
    user = auth_stack.seed(UserRole.STAFF)
    missing_origin = auth_client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": PASSWORD},
    )
    wrong = login(auth_client, user.email, "WrongPassword1!")
    unknown = login(auth_client, "missing@example.invalid", "WrongPassword1!")

    assert missing_origin.status_code == 403
    assert missing_origin.json()["error"]["code"] == "INVALID_ORIGIN"
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert unknown.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert user.email not in wrong.text + unknown.text


def test_refresh_API는_rotation과_재사용_차단을_적용한다(
    auth_client: TestClient,
    auth_stack: AuthStack,
) -> None:
    user = auth_stack.seed(UserRole.ADMIN)
    assert login(auth_client, user.email).status_code == 200
    original = auth_client.cookies[REFRESH_COOKIE]

    refreshed = auth_client.post("/api/v1/auth/refresh", headers=csrf_headers(auth_client))
    rotated = auth_client.cookies[REFRESH_COOKIE]
    assert refreshed.status_code == 200
    assert rotated != original

    auth_client.cookies.set(REFRESH_COOKIE, original)
    reused = auth_client.post("/api/v1/auth/refresh", headers=csrf_headers(auth_client))
    assert reused.status_code == 401
    assert reused.json()["error"]["code"] == "REFRESH_TOKEN_REUSE_DETECTED"

    auth_client.cookies.set(REFRESH_COOKIE, rotated)
    family_revoked = auth_client.post(
        "/api/v1/auth/refresh", headers=csrf_headers(auth_client)
    )
    assert family_revoked.status_code == 401


def test_logout은_CSRF를_검증하고_cookie를_제거한다(
    auth_client: TestClient,
    auth_stack: AuthStack,
) -> None:
    user = auth_stack.seed(UserRole.STUDENT)
    assert login(auth_client, user.email).status_code == 200

    denied = auth_client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN})
    assert denied.status_code == 403
    response = auth_client.post("/api/v1/auth/logout", headers=csrf_headers(auth_client))

    assert response.status_code == 204
    assert ACCESS_COOKIE not in auth_client.cookies
    assert REFRESH_COOKIE not in auth_client.cookies
    assert CSRF_COOKIE not in auth_client.cookies


def test_login_화면은_정상_실패_잠금_제출중_상태를_표현한다(
    auth_client: TestClient,
    auth_stack: AuthStack,
) -> None:
    user = auth_stack.seed(UserRole.STAFF)
    page = auth_client.get("/login")
    assert page.status_code == 200
    assert 'name="email"' in page.text
    assert "로그인 중" in page.text

    failure = auth_client.post(
        "/login",
        headers={"Origin": ORIGIN},
        data={"email": user.email, "password": "wrong", "next": "/events"},
    )
    locked = auth_client.post(
        "/login",
        headers={"Origin": ORIGIN},
        data={"email": user.email, "password": "wrong", "next": "/events"},
    )
    assert failure.status_code == 401
    assert "이메일 또는 비밀번호" in failure.text
    assert locked.status_code == 401
    assert 'data-account-locked="true"' in locked.text


@pytest.mark.parametrize(
    ("role", "expected_api", "expected_page"),
    [
        (UserRole.STUDENT, 403, 403),
        (UserRole.STAFF, 403, 403),
        (UserRole.ADMIN, 200, 200),
        (UserRole.SYSTEM_OPERATOR, 200, 200),
    ],
)
def test_역할별_API와_page_권한표(
    auth_client: TestClient,
    auth_stack: AuthStack,
    role: UserRole,
    expected_api: int,
    expected_page: int,
) -> None:
    user = auth_stack.seed(role)
    assert login(auth_client, user.email).status_code == 200

    assert auth_client.get("/api/v1/users").status_code == expected_api
    assert auth_client.get("/admin/users").status_code == expected_page
    assert auth_client.post(
        "/api/v1/auth/logout", headers=csrf_headers(auth_client)
    ).status_code == 204


@pytest.mark.parametrize(
    "role",
    [
        UserRole.STUDENT,
        UserRole.STAFF,
        UserRole.ADMIN,
        UserRole.SYSTEM_OPERATOR,
    ],
)
def test_인증된_모든_역할은_events_공통_shell을_렌더링한다(
    auth_client: TestClient,
    auth_stack: AuthStack,
    role: UserRole,
) -> None:
    user = auth_stack.seed(role)
    assert login(auth_client, user.email).status_code == 200

    response = auth_client.get("/events")

    assert response.status_code == 200
    assert user.name in response.text
    assert 'aria-label="주 탐색"' in response.text


def test_mock_입력_허용_환경은_관리자_운영_메뉴를_모든_page에_표시한다(
    auth_client: TestClient,
    auth_stack: AuthStack,
) -> None:
    development_settings = get_settings().model_copy(
        update={"mock_inputs_enabled": True}
    )
    app.dependency_overrides[get_settings] = lambda: development_settings
    operator = auth_stack.seed(UserRole.SYSTEM_OPERATOR)
    assert login(auth_client, operator.email).status_code == 200

    response = auth_client.get("/events")

    assert response.status_code == 200
    assert 'href="/admin/dev-tools"' in response.text
    assert 'href="/admin/mock-deliveries"' in response.text


def test_보호_page는_원래_경로와_함께_login으로_보낸다(
    auth_client: TestClient,
) -> None:
    response = auth_client.get(
        "/admin/users?search=virtual",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login?next=%2Fadmin%2Fusers")
