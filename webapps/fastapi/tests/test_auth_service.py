"""인증 service와 보안 primitive 단위 테스트."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.auth.errors import (
    AccountLockedError,
    AuthenticationRequiredError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    LoginRateLimitedError,
    RefreshTokenReuseError,
)
from app.auth.models import LoginCommand
from app.shared.security import canonicalize_email, validate_password_policy
from app.users.models import UserRole, UserStatus
from tests.auth_helpers import build_auth_stack


def login_command(email: str, password: str = "ValidPassword1!", ip: str = "ip-a"):
    return LoginCommand(email=email, password=password, ip_fingerprint=ip)


def test_password_hash_verify_email_canonicalization과_policy() -> None:
    stack = build_auth_stack()
    password_hash = stack.passwords.hash_password("ValidPassword1!")

    assert password_hash != "ValidPassword1!"
    assert stack.passwords.verify_password("ValidPassword1!", password_hash)
    assert not stack.passwords.verify_password("wrong", password_hash)
    assert not stack.passwords.verify_password("password", "malformed-hash")
    assert canonicalize_email("  ADMIN@Example.Invalid ") == "admin@example.invalid"
    assert validate_password_policy("short", minimum_length=12)
    assert validate_password_policy("ValidPassword1!", minimum_length=12) == ()


def test_정상_로그인과_access_인증() -> None:
    stack = build_auth_stack()
    user = stack.seed(UserRole.ADMIN)

    session = stack.auth_service.login(login_command(user.email.upper()))

    assert session.user.id == user.id
    assert stack.auth_service.authenticate_access_token(
        session.tokens.access_token
    ).id == user.id
    saved = stack.users.get_user(user.id)
    assert saved is not None
    assert saved.last_login_at == stack.clock.value
    assert saved.failed_login_count == 0


def test_credential_오류는_존재_여부와_비활성_상태를_숨긴다() -> None:
    stack = build_auth_stack()
    user = stack.seed(UserRole.STAFF)
    inactive = replace(
        user,
        status=UserStatus.INACTIVE,
        version=user.version + 1,
    )
    assert stack.users.replace_user(inactive, expected_version=user.version)

    with pytest.raises(InvalidCredentialsError):
        stack.auth_service.login(login_command("missing@example.invalid"))
    with pytest.raises(InvalidCredentialsError):
        stack.auth_service.login(login_command(user.email))


def test_로그인_실패가_누적되면_단기_잠금되고_시간_후_해제된다() -> None:
    stack = build_auth_stack(account_max_failures=2)
    user = stack.seed(UserRole.STUDENT)

    with pytest.raises(InvalidCredentialsError):
        stack.auth_service.login(login_command(user.email, "wrong-1"))
    with pytest.raises(AccountLockedError):
        stack.auth_service.login(login_command(user.email, "wrong-2"))
    assert "USER_LOCKED" in {log.action for log in stack.audit.list_all()}
    with pytest.raises(AccountLockedError):
        stack.auth_service.login(login_command(user.email))

    stack.clock.advance(seconds=121)
    session = stack.auth_service.login(login_command(user.email))
    assert session.user.status == UserStatus.ACTIVE
    assert "USER_UNLOCKED" in {log.action for log in stack.audit.list_all()}


def test_IP_실패_rate_limit은_원문_IP가_아닌_지문을_기준으로_동작한다() -> None:
    stack = build_auth_stack(ip_max_failures=2)
    for email in ("missing-1@example.invalid", "missing-2@example.invalid"):
        with pytest.raises(InvalidCredentialsError):
            stack.auth_service.login(login_command(email, ip="fingerprint-a"))

    with pytest.raises(LoginRateLimitedError):
        stack.auth_service.login(
            login_command("missing-3@example.invalid", ip="fingerprint-a")
        )


def test_refresh_rotation과_재사용_탐지는_family_전체를_폐기한다() -> None:
    stack = build_auth_stack()
    user = stack.seed(UserRole.ADMIN)
    original = stack.auth_service.login(login_command(user.email))

    rotated = stack.auth_service.refresh(original.tokens.refresh_token)
    assert rotated.tokens.refresh_token != original.tokens.refresh_token

    with pytest.raises(RefreshTokenReuseError):
        stack.auth_service.refresh(original.tokens.refresh_token)
    with pytest.raises(InvalidRefreshTokenError):
        stack.auth_service.refresh(rotated.tokens.refresh_token)


def test_refresh_만료_logout_폐기와_access_만료를_거부한다() -> None:
    stack = build_auth_stack(access_ttl_seconds=60, refresh_ttl_seconds=120)
    user = stack.seed(UserRole.STAFF)
    session = stack.auth_service.login(login_command(user.email))
    stack.auth_service.logout(session.tokens.refresh_token)
    with pytest.raises(InvalidRefreshTokenError):
        stack.auth_service.refresh(session.tokens.refresh_token)

    replacement = stack.auth_service.login(login_command(user.email, ip="ip-b"))
    stack.clock.advance(seconds=121)
    with pytest.raises(InvalidRefreshTokenError):
        stack.auth_service.refresh(replacement.tokens.refresh_token)
    with pytest.raises(AuthenticationRequiredError):
        stack.auth_service.authenticate_access_token(replacement.tokens.access_token)
