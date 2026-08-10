"""사용자 관리 service의 권한·동시성·감사 규칙 테스트."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.audit.service import AuditService
from app.auth.errors import InvalidRefreshTokenError, PermissionDeniedError
from app.auth.models import LoginCommand
from app.users.errors import (
    CurrentPasswordMismatchError,
    PasswordPolicyError,
    SelfDeactivationError,
    UserConcurrentUpdateError,
    UserEmailConflictError,
)
from app.users.models import (
    PRODUCT_ROLES,
    ChangePasswordCommand,
    CreateUserCommand,
    UpdateUserCommand,
    UserRole,
    UserStatus,
)
from app.users.seed import VirtualSeedPasswords, seed_virtual_users
from tests.helpers.auth import build_auth_stack


def operation_id() -> str:
    return str(uuid4())


def test_환경_주입_password로_세_제품_역할의_가상_사용자를_idempotent_seed한다() -> None:
    stack = build_auth_stack()
    passwords = VirtualSeedPasswords(
        student="StudentPassword1!",
        staff="StaffPassword12!",
        admin="AdminPassword12!",
    )

    first = seed_virtual_users(stack.user_service, passwords)
    second = seed_virtual_users(stack.user_service, passwords)
    fresh = seed_virtual_users(build_auth_stack().user_service, passwords)

    assert {user.role for user in first} == PRODUCT_ROLES
    assert [user.id for user in first] == [user.id for user in second]
    assert [user.id for user in first] == [user.id for user in fresh]
    assert all(user.email.endswith("@example.invalid") for user in first)


def test_ADMIN_이상만_사용자를_조회하고_생성할_수_있다() -> None:
    stack = build_auth_stack()
    student = stack.seed(UserRole.STUDENT)
    admin = stack.seed(UserRole.ADMIN)

    with pytest.raises(PermissionDeniedError):
        stack.user_service.list_users(student, limit=50, offset=0)

    created = stack.user_service.create_user(
        admin,
        CreateUserCommand(
            email=" NEW.USER@Example.Invalid ",
            password="AnotherValid1!",
            name="가상 사용자",
            role=UserRole.STAFF,
            operation_id=operation_id(),
        ),
        ip_fingerprint="fingerprint",
    )
    assert created.email == "new.user@example.invalid"
    assert created.password_hash != "AnotherValid1!"


def test_email_unique와_password_policy를_검증한다() -> None:
    stack = build_auth_stack()
    admin = stack.seed(UserRole.ADMIN)

    with pytest.raises(UserEmailConflictError):
        stack.user_service.create_user(
            admin,
            CreateUserCommand(
                email=admin.email.upper(),
                password="AnotherValid1!",
                name="duplicate",
                role=UserRole.STAFF,
                operation_id=operation_id(),
            ),
            ip_fingerprint=None,
        )
    with pytest.raises(PasswordPolicyError):
        stack.user_service.create_user(
            admin,
            CreateUserCommand(
                email="weak@example.invalid",
                password="weak",
                name="weak",
                role=UserRole.STAFF,
                operation_id=operation_id(),
            ),
            ip_fingerprint=None,
        )


def test_허용_필드만_CAS로_수정하고_중복_operation은_같은_결과를_낸다() -> None:
    stack = build_auth_stack()
    admin = stack.seed(UserRole.ADMIN)
    target = stack.seed(UserRole.STAFF)
    command = UpdateUserCommand(
        user_id=target.id,
        expected_version=target.version,
        operation_id=operation_id(),
        name="수정된 가상 직원",
        role=UserRole.ADMIN,
    )

    updated = stack.user_service.update_user(admin, command, ip_fingerprint="fingerprint")
    repeated = stack.user_service.update_user(admin, command, ip_fingerprint="fingerprint")

    assert updated == repeated
    assert updated.role == UserRole.ADMIN
    assert updated.name == "수정된 가상 직원"
    with pytest.raises(UserConcurrentUpdateError):
        stack.user_service.update_user(
            admin,
            UpdateUserCommand(
                user_id=target.id,
                expected_version=target.version,
                operation_id=operation_id(),
                name="stale",
            ),
            ip_fingerprint=None,
        )


def test_soft_deactivate는_본인을_보호하고_legacy_operator_정리를_허용한다() -> None:
    stack = build_auth_stack()
    admin = stack.seed(UserRole.ADMIN)
    operator = stack.seed(UserRole.SYSTEM_OPERATOR, email="legacy-operator@example.invalid")
    migrating = stack.seed(UserRole.SYSTEM_OPERATOR, email="migrating-operator@example.invalid")

    with pytest.raises(SelfDeactivationError):
        stack.user_service.deactivate_user(
            admin,
            admin.id,
            operation_id=operation_id(),
            ip_fingerprint=None,
        )
    inactive = stack.user_service.deactivate_user(
        admin,
        operator.id,
        operation_id=operation_id(),
        ip_fingerprint=None,
    )
    migrated = stack.user_service.update_user(
        admin,
        UpdateUserCommand(
            user_id=migrating.id,
            expected_version=migrating.version,
            operation_id=operation_id(),
            role=UserRole.ADMIN,
        ),
        ip_fingerprint=None,
    )
    assert inactive.status == UserStatus.INACTIVE
    assert migrated.role == UserRole.ADMIN


def test_비밀번호_변경은_기존값을_확인하고_전체_refresh를_폐기한다() -> None:
    stack = build_auth_stack()
    user = stack.seed(UserRole.STAFF)
    session = stack.auth_service.login(LoginCommand(user.email, "ValidPassword1!", "fingerprint-a"))
    with pytest.raises(CurrentPasswordMismatchError):
        stack.user_service.change_password(
            session.user,
            ChangePasswordCommand("wrong", "NewValidPassword2!", operation_id()),
            ip_fingerprint=None,
        )

    changed = stack.user_service.change_password(
        session.user,
        ChangePasswordCommand("ValidPassword1!", "NewValidPassword2!", operation_id()),
        ip_fingerprint="fingerprint-a",
    )
    assert stack.passwords.verify_password("NewValidPassword2!", changed.password_hash)
    with pytest.raises(InvalidRefreshTokenError):
        stack.auth_service.refresh(session.tokens.refresh_token)


def test_audit_log는_민감한_key와_원문_IP를_남기지_않는다() -> None:
    stack = build_auth_stack()
    service = AuditService(stack.audit, clock=stack.clock)
    log = service.record(
        operation_id=operation_id(),
        actor_user_id=None,
        action="SANITIZE_TEST",
        resource_type="user",
        resource_id="virtual-user",
        before={"password": "raw", "nested": {"access_token": "raw"}},
        after={"status": "ACTIVE", "cookie_value": "raw"},
        ip_fingerprint="hmac-fingerprint-only",
    )

    assert log.before == {"nested": {}}
    assert log.after == {"status": "ACTIVE"}
    assert log.ip_fingerprint == "hmac-fingerprint-only"
    assert "raw" not in repr(log)
