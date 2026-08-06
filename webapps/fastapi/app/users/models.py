"""사용자 도메인 값과 command."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class UserRole(StrEnum):
    STUDENT = "STUDENT"
    STAFF = "STAFF"
    ADMIN = "ADMIN"
    SYSTEM_OPERATOR = "SYSTEM_OPERATOR"


class UserStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    LOCKED = "LOCKED"


ADMIN_ROLES = frozenset({UserRole.ADMIN, UserRole.SYSTEM_OPERATOR})


@dataclass(frozen=True)
class User:
    id: str
    email: str
    password_hash: str
    name: str
    role: UserRole
    status: UserStatus
    failed_login_count: int
    locked_until: datetime | None
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime
    version: int
    created_operation_id: str
    last_operation_id: str
    must_change_password: bool = False
    password_changed_at: datetime | None = None


@dataclass(frozen=True)
class UserPage:
    items: list[User]
    total: int


@dataclass(frozen=True)
class CreateUserCommand:
    email: str
    password: str
    name: str
    role: UserRole
    operation_id: str


@dataclass(frozen=True)
class UpdateUserCommand:
    user_id: str
    expected_version: int
    operation_id: str
    email: str | None = None
    name: str | None = None
    role: UserRole | None = None
    status: UserStatus | None = None


@dataclass(frozen=True)
class ChangePasswordCommand:
    current_password: str
    new_password: str
    operation_id: str
