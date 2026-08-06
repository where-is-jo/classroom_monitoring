"""사용자 저장소 외부 I/O 포트."""

from __future__ import annotations

from typing import Protocol

from .models import User, UserPage, UserRole, UserStatus


class UserRepository(Protocol):
    def list_users(
        self,
        *,
        limit: int,
        offset: int,
        role: UserRole | None,
        status: UserStatus | None,
        search: str | None,
    ) -> UserPage: ...

    def get_user(self, user_id: str) -> User | None: ...

    def get_user_by_email(self, email: str) -> User | None: ...

    def get_user_by_operation_id(self, operation_id: str) -> User | None: ...

    def create_user(self, user: User) -> User: ...

    def replace_user(self, user: User, *, expected_version: int) -> User | None: ...

    def count_active_system_operators(self) -> int: ...


class StaffAssignmentPolicy(Protocol):
    def unlink_staff_user(
        self,
        actor: User,
        user_id: str,
        *,
        operation_id: str,
        ip_fingerprint: str | None,
    ) -> None: ...
