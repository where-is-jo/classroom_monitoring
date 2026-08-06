"""면담 대기 테스트용 외부 의존 없는 조립 helper."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from app.interview_waits.adapters.memory_repository import InMemoryInterviewWaitRepository
from app.interview_waits.models import CreateInterviewWaitCommand, InterviewWait
from app.interview_waits.service import EmployeeInterviewCoordinator, InterviewWaitService
from app.notifications.adapters.memory_repository import InMemoryNotificationRepository
from app.notifications.service import NotificationService
from app.users.models import User
from tests.employee_helpers import EmployeeStack, build_employee_stack


@dataclass
class InterviewWaitStack:
    employees: EmployeeStack
    waits: InMemoryInterviewWaitRepository
    notifications: InMemoryNotificationRepository
    notification_service: NotificationService
    service: InterviewWaitService
    coordinator: EmployeeInterviewCoordinator

    def create_wait(
        self,
        employee_id: str,
        *,
        actor: User | None = None,
        message: str | None = "면담을 요청합니다.",
        operation_id: str | None = None,
    ) -> InterviewWait:
        return self.service.create_wait(
            actor or self.employees.student,
            CreateInterviewWaitCommand(
                employee_id=employee_id,
                message=message,
                operation_id=operation_id or str(uuid4()),
            ),
        )


def build_interview_wait_stack(
    *,
    notification_repository: InMemoryNotificationRepository | None = None,
) -> InterviewWaitStack:
    employees = build_employee_stack()
    waits = InMemoryInterviewWaitRepository()
    notifications = notification_repository or InMemoryNotificationRepository()
    notification_service = NotificationService(
        notifications,
        employees.auth.users,
        clock=employees.auth.clock,
        mock_delivery_mode=None,
    )
    service = InterviewWaitService(
        waits,
        employees.employees,
        employees.auth.users,
        notification_service,
        expires_after_hours=24,
        clock=employees.auth.clock,
    )
    return InterviewWaitStack(
        employees=employees,
        waits=waits,
        notifications=notifications,
        notification_service=notification_service,
        service=service,
        coordinator=EmployeeInterviewCoordinator(employees.service, service),
    )
