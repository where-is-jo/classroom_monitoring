"""인증·사용자 테스트에서 공유하는 외부 의존 없는 조립 helper."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydantic import SecretStr

from app.audit.adapters.memory_repository import InMemoryAuditRepository
from app.audit.service import AuditService
from app.auth.adapters.memory_repository import InMemoryAuthRepository
from app.auth.service import AuthService, LoginRateLimiter
from app.shared.security import PasswordSecurity, TokenSecurity
from app.users.adapters.memory_repository import InMemoryUserRepository
from app.users.models import CreateUserCommand, User, UserRole
from app.users.service import UserService


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, **kwargs: int) -> None:
        self.value += timedelta(**kwargs)


@dataclass
class AuthStack:
    clock: MutableClock
    users: InMemoryUserRepository
    auth: InMemoryAuthRepository
    audit: InMemoryAuditRepository
    passwords: PasswordSecurity
    user_service: UserService
    auth_service: AuthService

    def seed(
        self,
        role: UserRole,
        *,
        email: str | None = None,
        password: str = "ValidPassword1!",
        name: str | None = None,
    ) -> User:
        return self.user_service.seed_user(
            CreateUserCommand(
                email=email or f"{role.value.lower()}@example.invalid",
                password=password,
                name=name or role.value,
                role=role,
                operation_id=str(uuid4()),
            )
        )


def build_auth_stack(
    *,
    account_max_failures: int = 3,
    ip_max_failures: int = 10,
    access_ttl_seconds: int = 300,
    refresh_ttl_seconds: int = 3600,
) -> AuthStack:
    clock = MutableClock()
    users = InMemoryUserRepository()
    auth = InMemoryAuthRepository()
    audit = InMemoryAuditRepository()
    passwords = PasswordSecurity()
    audit_service = AuditService(audit, clock=clock)
    user_service = UserService(
        users,
        auth,
        audit_service,
        passwords,
        password_min_length=12,
        clock=clock,
    )
    token_security = TokenSecurity(
        access_secret=SecretStr("test-access-secret-at-least-32-characters"),
        refresh_secret=SecretStr("test-refresh-secret-at-least-32-characters"),
        access_ttl_seconds=access_ttl_seconds,
        refresh_ttl_seconds=refresh_ttl_seconds,
    )
    auth_service = AuthService(
        users,
        auth,
        audit_service,
        passwords,
        token_security,
        LoginRateLimiter(max_failures=ip_max_failures, window_seconds=60),
        account_max_failures=account_max_failures,
        lockout_seconds=120,
        clock=clock,
    )
    return AuthStack(
        clock=clock,
        users=users,
        auth=auth,
        audit=audit,
        passwords=passwords,
        user_service=user_service,
        auth_service=auth_service,
    )
