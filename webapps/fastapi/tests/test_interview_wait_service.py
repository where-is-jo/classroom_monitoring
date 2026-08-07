"""면담 대기 전이·권한·직원 복귀 조정 규칙 테스트."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from app.auth.errors import PermissionDeniedError
from app.employees.models import (
    EmployeeObservation,
    EmployeeStatus,
    RecordEmployeeObservationCommand,
    SetStatusOverrideCommand,
)
from app.interview_waits.errors import (
    InterviewWaitDuplicateError,
    InterviewWaitTransitionError,
)
from app.interview_waits.models import (
    CreateInterviewWaitCommand,
    EvaluateInterviewWaitExpirationsCommand,
    InterviewWaitStatus,
    TransitionInterviewWaitCommand,
)
from app.interview_waits.service import EmployeeInterviewCoordinator, InterviewWaitService
from app.notifications.adapters.memory_repository import InMemoryNotificationRepository
from app.notifications.models import Notification
from app.notifications.service import NotificationService
from app.shared.errors import RepositoryUnavailableError
from app.users.models import UserRole
from tests.interview_wait_helpers import InterviewWaitStack, build_interview_wait_stack


@pytest.fixture
def stack() -> InterviewWaitStack:
    return build_interview_wait_stack()


def _observe(
    stack: InterviewWaitStack,
    employee_id: str,
    *,
    event_id: str,
    person_present: bool,
    phone_detected: bool = False,
) -> EmployeeObservation:
    return stack.coordinator.record_mock_observation(
        stack.employees.admin,
        RecordEmployeeObservationCommand(
            event_id=event_id,
            employee_id=employee_id,
            person_present=person_present,
            phone_detected=phone_detected,
            confidence=0.95,
            observed_at=stack.employees.auth.clock(),
        ),
    )


def test_부재직원은_WAITING_재석직원은_즉시_READY이며_활성중복은_거부된다(
    stack: InterviewWaitStack,
) -> None:
    absent = stack.employees.create_employee(employee_no="EMP-001")
    waiting = stack.create_wait(absent.id, operation_id="wait-create")

    assert waiting.status == InterviewWaitStatus.WAITING
    with pytest.raises(InterviewWaitDuplicateError):
        stack.create_wait(absent.id, operation_id="duplicate-create")

    present = stack.employees.create_employee(employee_no="EMP-002")
    _observe(stack, present.id, event_id="present-before-create", person_present=True)
    ready = stack.create_wait(present.id, operation_id="ready-create")

    assert ready.status == InterviewWaitStatus.READY
    assert ready.ready_at == stack.employees.auth.clock()
    assert stack.notifications.count_unread(stack.employees.student.id) == 1
    assert len(stack.waits.list_history(ready.id)) == 1


def test_동시_활성대기_생성은_memory_repository에서도_한건만_성공한다(
    stack: InterviewWaitStack,
) -> None:
    employee = stack.employees.create_employee()

    def create(operation_id: str) -> str:
        try:
            return stack.create_wait(employee.id, operation_id=operation_id).id
        except InterviewWaitDuplicateError:
            return "duplicate"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create, ("race-create-1", "race-create-2")))

    assert results.count("duplicate") == 1
    page = stack.waits.list_waits(
        requester_user_id=stack.employees.student.id,
        employee_id=employee.id,
        status=None,
        limit=50,
        offset=0,
    )
    assert page.total == 1


@pytest.mark.parametrize(
    ("initial", "target", "allowed"),
    [
        (InterviewWaitStatus.WAITING, InterviewWaitStatus.CANCELLED, True),
        (InterviewWaitStatus.WAITING, InterviewWaitStatus.COMPLETED, False),
        (InterviewWaitStatus.READY, InterviewWaitStatus.CANCELLED, True),
        (InterviewWaitStatus.READY, InterviewWaitStatus.COMPLETED, True),
    ],
)
def test_허용_금지_상태전이표(
    stack: InterviewWaitStack,
    initial: InterviewWaitStatus,
    target: InterviewWaitStatus,
    allowed: bool,
) -> None:
    employee = stack.employees.create_employee(employee_no=f"EMP-{uuid4()}")
    if initial == InterviewWaitStatus.READY:
        _observe(stack, employee.id, event_id=str(uuid4()), person_present=True)
    wait = stack.create_wait(employee.id)
    command = TransitionInterviewWaitCommand(
        wait_id=wait.id,
        status=target,
        operation_id=str(uuid4()),
    )

    if allowed:
        changed = stack.service.transition_wait(stack.employees.student, command)
        repeated = stack.service.transition_wait(
            stack.employees.student,
            TransitionInterviewWaitCommand(
                wait_id=wait.id,
                status=target,
                operation_id=str(uuid4()),
            ),
        )
        assert changed.status == target
        assert repeated == changed
        assert len(stack.waits.list_history(wait.id)) == 2
    else:
        with pytest.raises(InterviewWaitTransitionError):
            stack.service.transition_wait(stack.employees.student, command)


def test_요청자_대상STAFF_다른STAFF_ADMIN_권한(
    stack: InterviewWaitStack,
) -> None:
    employee = stack.employees.create_employee(user_id=stack.employees.staff.id)
    other_staff = stack.employees.auth.seed(UserRole.STAFF, email="other-staff@example.invalid")
    wait = stack.create_wait(employee.id)

    assert stack.service.get_wait(stack.employees.student, wait.id) == wait
    assert stack.service.get_wait(stack.employees.staff, wait.id) == wait
    with pytest.raises(PermissionDeniedError):
        stack.service.get_wait(other_staff, wait.id)
    with pytest.raises(PermissionDeniedError):
        stack.service.get_wait(stack.employees.admin, wait.id)
    with pytest.raises(PermissionDeniedError):
        stack.service.transition_wait(
            other_staff,
            TransitionInterviewWaitCommand(wait.id, InterviewWaitStatus.CANCELLED, "other-cancel"),
        )
    with pytest.raises(PermissionDeniedError):
        stack.service.transition_wait(
            stack.employees.admin,
            TransitionInterviewWaitCommand(wait.id, InterviewWaitStatus.CANCELLED, "admin-cancel"),
        )


def test_면담_신청은_STUDENT만_가능하다(stack: InterviewWaitStack) -> None:
    employee = stack.employees.create_employee()
    command = CreateInterviewWaitCommand(employee.id, None, "role-create")

    for actor in (stack.employees.staff, stack.employees.admin):
        with pytest.raises(PermissionDeniedError):
            stack.service.create_wait(actor, command)


def test_STAFF가_프로필에서_WORKING으로_바꾸면_학생_대기가_READY가_된다(
    stack: InterviewWaitStack,
) -> None:
    employee = stack.employees.create_employee(user_id=stack.employees.staff.id)
    wait = stack.create_wait(employee.id)

    changed = stack.coordinator.set_status_override(
        stack.employees.staff,
        SetStatusOverrideCommand(
            employee_id=employee.id,
            status=EmployeeStatus.WORKING,
            reason=None,
            ends_at=None,
            expected_version=employee.version,
            operation_id="staff-profile-working",
        ),
        ip_fingerprint="test",
    )

    ready = stack.waits.get_wait(wait.id)
    assert changed.current_status.status == EmployeeStatus.WORKING
    assert ready is not None and ready.status == InterviewWaitStatus.READY
    assert stack.notifications.count_unread(stack.employees.student.id) == 1


def test_직원복귀에서만_READY_history_알림이_한번생성되고_재처리는_멱등이다(
    stack: InterviewWaitStack,
) -> None:
    employee = stack.employees.create_employee(user_id=stack.employees.staff.id)
    wait = stack.create_wait(employee.id)

    _observe(stack, employee.id, event_id="away-to-offsite", person_present=False)
    assert stack.waits.get_wait(wait.id).status == InterviewWaitStatus.WAITING  # type: ignore[union-attr]

    first = _observe(stack, employee.id, event_id="employee-return", person_present=True)
    repeated = _observe(stack, employee.id, event_id="employee-return", person_present=True)
    ready = stack.waits.get_wait(wait.id)

    assert first.resulting_status == repeated.resulting_status == EmployeeStatus.WORKING
    assert ready is not None and ready.status == InterviewWaitStatus.READY
    assert len(stack.waits.list_history(wait.id)) == 2
    assert stack.notifications.count_unread(stack.employees.student.id) == 1


def test_중간_알림실패후_같은_직원command_재시도는_누락알림만_보완한다() -> None:
    class FailOnceRepository(InMemoryNotificationRepository):
        def __init__(self) -> None:
            super().__init__()
            self.failed = False

        def create_notification(self, notification: Notification) -> Notification:
            if not self.failed:
                self.failed = True
                raise RepositoryUnavailableError()
            return super().create_notification(notification)

    base = build_interview_wait_stack()
    employees = base.employees
    waits = base.waits
    failing_notifications = FailOnceRepository()
    notification_service = NotificationService(
        failing_notifications,
        employees.auth.users,
        clock=employees.auth.clock,
        mock_delivery_mode=None,
    )
    wait_service = InterviewWaitService(
        waits,
        employees.employees,
        employees.auth.users,
        notification_service,
        expires_after_hours=24,
        clock=employees.auth.clock,
    )
    coordinator = EmployeeInterviewCoordinator(employees.service, wait_service)
    employee = employees.create_employee()
    wait = wait_service.create_wait(
        employees.student,
        CreateInterviewWaitCommand(employee.id, None, "wait-create"),
    )
    command = RecordEmployeeObservationCommand(
        event_id="return-with-failure",
        employee_id=employee.id,
        person_present=True,
        phone_detected=False,
        confidence=0.95,
        observed_at=employees.auth.clock(),
    )

    with pytest.raises(RepositoryUnavailableError):
        coordinator.record_mock_observation(employees.admin, command)
    coordinator.record_mock_observation(employees.admin, command)

    assert waits.get_wait(wait.id).status == InterviewWaitStatus.READY  # type: ignore[union-attr]
    assert len(waits.list_history(wait.id)) == 2
    assert failing_notifications.count_unread(employees.student.id) == 1


def test_목록GET은_만료경계에서_무상태이고_명시적평가가_EXPIRED를_기록한다(
    stack: InterviewWaitStack,
) -> None:
    employee = stack.employees.create_employee()
    wait = stack.create_wait(employee.id)
    stack.employees.auth.clock.advance(hours=24)

    page = stack.service.list_requester_waits(
        stack.employees.student,
        status=None,
        limit=50,
        offset=0,
    )
    assert page.items[0].status == InterviewWaitStatus.WAITING
    assert len(stack.waits.list_history(wait.id)) == 1

    result = stack.service.evaluate_expirations(
        stack.employees.admin,
        EvaluateInterviewWaitExpirationsCommand(operation_id="expire-all"),
    )
    assert result.evaluated_count == result.expired_count == 1
    assert stack.waits.get_wait(wait.id).status == InterviewWaitStatus.EXPIRED  # type: ignore[union-attr]
    assert len(stack.waits.list_history(wait.id)) == 2


def test_직원비활성화는_활성대기를_취소한뒤_직원을_비활성화한다(
    stack: InterviewWaitStack,
) -> None:
    employee = stack.employees.create_employee()
    wait = stack.create_wait(employee.id)

    inactive = stack.coordinator.deactivate_employee(
        stack.employees.admin,
        employee.id,
        expected_version=employee.version,
        operation_id="deactivate-employee",
        ip_fingerprint="test-ip",
    )

    assert inactive.is_active is False
    assert stack.waits.get_wait(wait.id).status == InterviewWaitStatus.CANCELLED  # type: ignore[union-attr]
