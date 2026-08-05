"""환경에서 받은 password로만 네 역할의 가상 사용자를 만드는 helper."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import NAMESPACE_URL, uuid5

from .models import CreateUserCommand, User, UserRole
from .service import UserService


@dataclass(frozen=True)
class VirtualSeedPasswords:
    student: str
    staff: str
    admin: str
    system_operator: str


_VIRTUAL_USERS = (
    ("student@example.invalid", "가상 학생", UserRole.STUDENT, "student"),
    ("staff@example.invalid", "가상 직원", UserRole.STAFF, "staff"),
    ("admin@example.invalid", "가상 관리자", UserRole.ADMIN, "admin"),
    (
        "operator@example.invalid",
        "가상 시스템 운영자",
        UserRole.SYSTEM_OPERATOR,
        "system_operator",
    ),
)


def seed_virtual_users(
    service: UserService,
    passwords: VirtualSeedPasswords,
) -> list[User]:
    seeded: list[User] = []
    for email, name, role, password_field in _VIRTUAL_USERS:
        operation_id = str(uuid5(NAMESPACE_URL, f"smart-office-seed:{email}"))
        seeded.append(
            service.seed_user(
                CreateUserCommand(
                    email=email,
                    password=getattr(passwords, password_field),
                    name=name,
                    role=role,
                    operation_id=operation_id,
                )
            )
        )
    return seeded
