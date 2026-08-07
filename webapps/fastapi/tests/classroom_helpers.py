"""Reusable classroom service test stack."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from app.audit.service import AuditService
from app.classrooms.adapters.memory_repository import InMemoryClassroomRepository
from app.classrooms.models import (
    Classroom,
    CreateClassroomCommand,
    CreateSeatCommand,
    Seat,
    SeatGeometry,
)
from app.classrooms.service import ClassroomService
from app.notifications.adapters.memory_repository import InMemoryNotificationRepository
from app.notifications.service import NotificationService
from app.users.models import User
from tests.auth_helpers import AuthStack, build_auth_stack


@dataclass
class ClassroomStack:
    auth: AuthStack
    repository: InMemoryClassroomRepository
    notifications: InMemoryNotificationRepository
    notification_service: NotificationService
    service: ClassroomService
    admin: User
    student: User

    def create_classroom(
        self,
        *,
        code: str = "ROOM-101",
        timezone: str = "Asia/Seoul",
        grace: int = 10,
        responsible_staff_user_ids: tuple[str, ...] = (),
    ) -> Classroom:
        return self.service.create_classroom(
            self.admin,
            CreateClassroomCommand(
                code=code,
                name=f"Classroom {code}",
                location="Building A",
                timezone=timezone,
                after_hours_grace_minutes=grace,
                operation_id=str(uuid4()),
                responsible_staff_user_ids=responsible_staff_user_ids,
            ),
            ip_fingerprint="test-ip",
        )

    def create_seat(
        self,
        classroom_id: str,
        *,
        code: str = "A-1",
        geometry: SeatGeometry | None = None,
    ) -> Seat:
        return self.service.create_seat(
            self.admin,
            CreateSeatCommand(
                classroom_id=classroom_id,
                code=code,
                label=f"Seat {code}",
                geometry=geometry,
                operation_id=str(uuid4()),
            ),
            ip_fingerprint="test-ip",
        )


def build_classroom_stack() -> ClassroomStack:
    from app.users.models import UserRole

    auth = build_auth_stack()
    admin = auth.seed(UserRole.ADMIN, email="classroom-admin@example.invalid")
    student = auth.seed(UserRole.STUDENT, email="classroom-student@example.invalid")
    repository = InMemoryClassroomRepository()
    notifications = InMemoryNotificationRepository()
    notification_service = NotificationService(
        notifications,
        auth.users,
        clock=auth.clock,
        mock_delivery_mode=None,
    )
    service = ClassroomService(
        repository,
        auth.users,
        notification_service,
        AuditService(auth.audit, clock=auth.clock),
        occupancy_confidence_threshold=0.6,
        clock=auth.clock,
    )
    stack = ClassroomStack(
        auth=auth,
        repository=repository,
        notifications=notifications,
        notification_service=notification_service,
        service=service,
        admin=admin,
        student=student,
    )
    return stack
