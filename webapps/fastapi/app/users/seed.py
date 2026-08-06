"""환경에서 받은 password로만 세 제품 역할의 가상 사용자를 만드는 helper."""

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


_VIRTUAL_USERS = (
    ("student@example.invalid", "데모 학생 01", UserRole.STUDENT, "student", "student-01"),
    ("staff@example.invalid", "데모 직원 01", UserRole.STAFF, "staff", "staff-01"),
    ("staff-02@example.invalid", "데모 직원 02", UserRole.STAFF, "staff", "staff-02"),
    ("admin@example.invalid", "데모 관리자 01", UserRole.ADMIN, "admin", "admin-01"),
)


def seed_virtual_users(
    service: UserService,
    passwords: VirtualSeedPasswords,
) -> list[User]:
    seeded: list[User] = []
    for email, name, role, password_field, entity_name in _VIRTUAL_USERS:
        operation_id = str(uuid5(NAMESPACE_URL, f"smart-office-seed:{email}"))
        seeded.append(
            service.seed_user(
                CreateUserCommand(
                    email=email,
                    password=getattr(passwords, password_field),
                    name=name,
                    role=role,
                    operation_id=operation_id,
                    entity_id=str(uuid5(NAMESPACE_URL, f"smart-office-demo-user:{entity_name}")),
                )
            )
        )
    return seeded
