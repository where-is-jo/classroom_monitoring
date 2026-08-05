"""UserRepository의 외부 의존 없는 memory 구현."""

from __future__ import annotations

from threading import RLock

from ..errors import UserEmailConflictError, UserOperationConflictError
from ..models import User, UserPage, UserRole, UserStatus


class InMemoryUserRepository:
    def __init__(self, users: list[User] | None = None) -> None:
        self._users = {user.id: user for user in users or []}
        self._lock = RLock()

    def list_users(
        self,
        *,
        limit: int,
        offset: int,
        role: UserRole | None,
        status: UserStatus | None,
        search: str | None,
    ) -> UserPage:
        with self._lock:
            users = list(self._users.values())
        if role is not None:
            users = [user for user in users if user.role == role]
        if status is not None:
            users = [user for user in users if user.status == status]
        if search:
            normalized_search = search.strip().lower()
            users = [
                user
                for user in users
                if normalized_search in user.email or normalized_search in user.name.lower()
            ]
        users.sort(key=lambda user: (user.created_at, user.id), reverse=True)
        return UserPage(items=users[offset : offset + limit], total=len(users))

    def get_user(self, user_id: str) -> User | None:
        with self._lock:
            return self._users.get(user_id)

    def get_user_by_email(self, email: str) -> User | None:
        with self._lock:
            return next((user for user in self._users.values() if user.email == email), None)

    def get_user_by_operation_id(self, operation_id: str) -> User | None:
        with self._lock:
            return next(
                (
                    user
                    for user in self._users.values()
                    if operation_id
                    in (user.created_operation_id, user.last_operation_id)
                ),
                None,
            )

    def create_user(self, user: User) -> User:
        with self._lock:
            existing_operation = self.get_user_by_operation_id(user.created_operation_id)
            if existing_operation is not None:
                if existing_operation.email != user.email:
                    raise UserOperationConflictError()
                return existing_operation
            if self.get_user_by_email(user.email) is not None:
                raise UserEmailConflictError()
            self._users[user.id] = user
            return user

    def replace_user(self, user: User, *, expected_version: int) -> User | None:
        with self._lock:
            current = self._users.get(user.id)
            if current is None or current.version != expected_version:
                return None
            email_owner = self.get_user_by_email(user.email)
            if email_owner is not None and email_owner.id != user.id:
                raise UserEmailConflictError()
            operation_owner = self.get_user_by_operation_id(user.last_operation_id)
            if operation_owner is not None and operation_owner.id != user.id:
                raise UserOperationConflictError()
            self._users[user.id] = user
            return user

    def count_active_system_operators(self) -> int:
        with self._lock:
            return sum(
                1
                for user in self._users.values()
                if user.role == UserRole.SYSTEM_OPERATOR
                and user.status == UserStatus.ACTIVE
            )
