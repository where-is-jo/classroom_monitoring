"""직원 프로필·상태 정책 서비스 단위 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from app.auth.errors import PermissionDeniedError
from app.employees.adapters.memory_repository import InMemoryEmployeeRepository
from app.employees.errors import (
    EmployeeConcurrentUpdateError,
    EmployeeInactiveError,
    EmployeeNumberConflictError,
    EmployeeUserLinkConflictError,
    InvalidEmployeeUserError,
)
from app.employees.models import (
    ClearStatusOverrideCommand,
    Employee,
    EmployeeObservation,
    EmployeeStatus,
    EmployeeStatusEvaluation,
    EmployeeStatusHistory,
    EvaluateEmployeeStatusesCommand,
    RecordEmployeeObservationCommand,
    SetStatusOverrideCommand,
    StatusSource,
    UpdateEmployeeCommand,
)
from app.users.models import UserRole
from tests.employee_helpers import EmployeeStack, build_employee_stack


@pytest.fixture
def employee_stack() -> EmployeeStack:
    return build_employee_stack()


def _observation(
    stack: EmployeeStack,
    employee_id: str,
    *,
    person_present: bool,
    phone_detected: bool = False,
    observed_at: datetime | None = None,
    event_id: str | None = None,
) -> EmployeeObservation:
    return stack.service.record_mock_observation(
        stack.admin,
        RecordEmployeeObservationCommand(
            event_id=event_id or str(uuid4()),
            employee_id=employee_id,
            person_present=person_present,
            phone_detected=phone_detected,
            confidence=0.95,
            observed_at=observed_at or stack.auth.clock(),
        ),
    )


def _evaluate(stack: EmployeeStack, operation_id: str | None = None) -> EmployeeStatusEvaluation:
    return stack.service.evaluate_statuses(
        stack.admin,
        EvaluateEmployeeStatusesCommand(operation_id=operation_id or str(uuid4())),
        ip_fingerprint="test-ip-fingerprint",
    )


def _history_count(stack: EmployeeStack, employee_id: str) -> int:
    return stack.service.list_status_history(stack.admin, employee_id, limit=200, offset=0).total


def _stored_employee(stack: EmployeeStack, employee_id: str) -> Employee:
    employee = stack.employees.get_employee(employee_id)
    assert employee is not None
    return employee


def test_직원_CRUD와_STAFF_0대1_연결_제약(employee_stack: EmployeeStack) -> None:
    employee = employee_stack.create_employee(user_id=employee_stack.staff.id)

    assert employee.current_status.status == EmployeeStatus.AWAY
    assert employee.current_status.source == StatusSource.SYSTEM
    assert _history_count(employee_stack, employee.id) == 1

    with pytest.raises(EmployeeNumberConflictError):
        employee_stack.create_employee(employee_no="emp-001")
    with pytest.raises(EmployeeUserLinkConflictError):
        employee_stack.create_employee(employee_no="EMP-002", user_id=employee_stack.staff.id)
    with pytest.raises(InvalidEmployeeUserError):
        employee_stack.create_employee(employee_no="EMP-003", user_id=employee_stack.student.id)

    updated = employee_stack.service.update_employee(
        employee_stack.admin,
        UpdateEmployeeCommand(
            employee_id=employee.id,
            expected_version=employee.version,
            operation_id=str(uuid4()),
            display_name="수정 직원",
            department="운영팀",
        ),
        ip_fingerprint="test-ip-fingerprint",
    )
    assert updated.display_name == "수정 직원"
    assert updated.department == "운영팀"
    assert updated.version == 1

    inactive = employee_stack.service.deactivate_employee(
        employee_stack.admin,
        employee.id,
        expected_version=updated.version,
        operation_id=str(uuid4()),
        ip_fingerprint="test-ip-fingerprint",
    )
    assert not inactive.is_active
    with pytest.raises(EmployeeInactiveError):
        _observation(employee_stack, employee.id, person_present=True)


def test_사람과_통화_조합_및_동일_상태_noop(employee_stack: EmployeeStack) -> None:
    employee = employee_stack.create_employee(user_id=employee_stack.staff.id)

    working = _observation(employee_stack, employee.id, person_present=True)
    assert working.resulting_status == EmployeeStatus.WORKING
    assert working.status_changed
    version_after_working = _stored_employee(employee_stack, employee.id).version

    repeated = _observation(employee_stack, employee.id, person_present=True)
    assert repeated.resulting_status == EmployeeStatus.WORKING
    assert not repeated.status_changed
    assert _history_count(employee_stack, employee.id) == 2
    assert _stored_employee(employee_stack, employee.id).version == version_after_working + 1

    on_call = _observation(employee_stack, employee.id, person_present=True, phone_detected=True)
    assert on_call.resulting_status == EmployeeStatus.ON_CALL
    assert _history_count(employee_stack, employee.id) == 3


def test_2분59초_3분_59분59초_60분_경계(employee_stack: EmployeeStack) -> None:
    employee = employee_stack.create_employee()
    seen_at = employee_stack.auth.clock()
    _observation(employee_stack, employee.id, person_present=True, observed_at=seen_at)

    employee_stack.auth.clock.value = seen_at + timedelta(minutes=2, seconds=59)
    assert _evaluate(employee_stack).changed_count == 0
    assert (
        _stored_employee(employee_stack, employee.id).current_status.status
        == EmployeeStatus.WORKING
    )

    employee_stack.auth.clock.value = seen_at + timedelta(minutes=3)
    assert _evaluate(employee_stack).changed_count == 1
    assert (
        _stored_employee(employee_stack, employee.id).current_status.status == EmployeeStatus.AWAY
    )

    employee_stack.auth.clock.value = seen_at + timedelta(minutes=59, seconds=59)
    assert _evaluate(employee_stack).changed_count == 0
    assert (
        _stored_employee(employee_stack, employee.id).current_status.status == EmployeeStatus.AWAY
    )

    employee_stack.auth.clock.value = seen_at + timedelta(minutes=60)
    assert _evaluate(employee_stack).changed_count == 1
    assert (
        _stored_employee(employee_stack, employee.id).current_status.status
        == EmployeeStatus.OFFSITE
    )


def test_GET은_상태_version_history를_변경하지_않는다(employee_stack: EmployeeStack) -> None:
    employee = employee_stack.create_employee()
    _observation(employee_stack, employee.id, person_present=True)
    before = _stored_employee(employee_stack, employee.id)
    before_history = _history_count(employee_stack, employee.id)
    employee_stack.auth.clock.advance(minutes=90)

    for _ in range(3):
        employee_stack.service.list_employees(employee_stack.staff, limit=50, offset=0)
        employee_stack.service.get_employee(employee_stack.staff, employee.id)
        employee_stack.service.list_status_history(
            employee_stack.staff, employee.id, limit=50, offset=0
        )

    assert _stored_employee(employee_stack, employee.id) == before
    assert _history_count(employee_stack, employee.id) == before_history


def test_override_우선과_해제_즉시_재평가_사용자_여정(
    employee_stack: EmployeeStack,
) -> None:
    employee = employee_stack.create_employee(user_id=employee_stack.staff.id)
    _observation(employee_stack, employee.id, person_present=True)
    current = _stored_employee(employee_stack, employee.id)

    overridden = employee_stack.service.set_status_override(
        employee_stack.staff,
        SetStatusOverrideCommand(
            employee_id=employee.id,
            status=EmployeeStatus.OFFSITE,
            reason="외부 일정",
            ends_at=None,
            expected_version=current.version,
            operation_id=str(uuid4()),
        ),
        ip_fingerprint="test-ip-fingerprint",
    )
    assert overridden.current_status.status == EmployeeStatus.OFFSITE

    employee_stack.auth.clock.advance(seconds=30)
    ignored = _observation(employee_stack, employee.id, person_present=True, phone_detected=True)
    assert ignored.resulting_status == EmployeeStatus.OFFSITE
    assert not ignored.status_changed
    during_override = _stored_employee(employee_stack, employee.id)
    assert during_override.current_status.status == EmployeeStatus.OFFSITE

    cleared = employee_stack.service.clear_status_override(
        employee_stack.staff,
        ClearStatusOverrideCommand(
            employee_id=employee.id,
            expected_version=during_override.version,
            operation_id=str(uuid4()),
        ),
        ip_fingerprint="test-ip-fingerprint",
    )
    assert cleared.active_override is None
    assert cleared.current_status.status == EmployeeStatus.ON_CALL
    assert _history_count(employee_stack, employee.id) == 4


def test_override_만료와_평가_operation_id_재시도는_이력을_중복하지_않는다(
    employee_stack: EmployeeStack,
) -> None:
    employee = employee_stack.create_employee(user_id=employee_stack.staff.id)
    _observation(employee_stack, employee.id, person_present=True)
    current = _stored_employee(employee_stack, employee.id)
    ends_at = employee_stack.auth.clock() + timedelta(seconds=30)
    employee_stack.service.set_status_override(
        employee_stack.staff,
        SetStatusOverrideCommand(
            employee_id=employee.id,
            status=EmployeeStatus.OFFSITE,
            reason="잠시 외부",
            ends_at=ends_at,
            expected_version=current.version,
            operation_id=str(uuid4()),
        ),
        ip_fingerprint="test-ip-fingerprint",
    )
    employee_stack.auth.clock.value = ends_at
    operation_id = str(uuid4())

    first = _evaluate(employee_stack, operation_id)
    history_after_first = _history_count(employee_stack, employee.id)
    second = _evaluate(employee_stack, operation_id)

    assert first.changed_count == second.changed_count == 1
    assert _history_count(employee_stack, employee.id) == history_after_first
    restored = _stored_employee(employee_stack, employee.id)
    assert restored.active_override is None
    assert restored.current_status.status == EmployeeStatus.WORKING


def test_동일_event_id와_오래된_관측은_최초_결과를_보존한다(
    employee_stack: EmployeeStack,
) -> None:
    employee = employee_stack.create_employee()
    first_id = str(uuid4())
    first = _observation(
        employee_stack,
        employee.id,
        person_present=True,
        observed_at=employee_stack.auth.clock(),
        event_id=first_id,
    )
    employee_stack.auth.clock.advance(seconds=10)
    _observation(
        employee_stack,
        employee.id,
        person_present=True,
        phone_detected=True,
    )
    version_before_old = _stored_employee(employee_stack, employee.id).version
    old = _observation(
        employee_stack,
        employee.id,
        person_present=True,
        observed_at=first.observed_at - timedelta(seconds=10),
    )
    duplicate = _observation(
        employee_stack,
        employee.id,
        person_present=False,
        observed_at=employee_stack.auth.clock() + timedelta(hours=1),
        event_id=first_id,
    )

    assert old.resulting_status == EmployeeStatus.ON_CALL
    assert not old.status_changed
    assert _stored_employee(employee_stack, employee.id).version == version_before_old
    assert duplicate == first
    assert (
        _stored_employee(employee_stack, employee.id).current_status.status
        == EmployeeStatus.ON_CALL
    )


class OneConflictRepository(InMemoryEmployeeRepository):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next_replace = False

    def replace_employee(
        self,
        employee: Employee,
        *,
        expected_version: int,
        history: EmployeeStatusHistory | None,
    ) -> Employee | None:
        if self.fail_next_replace:
            self.fail_next_replace = False
            return None
        return super().replace_employee(
            employee, expected_version=expected_version, history=history
        )


def test_mock_관측_CAS_충돌은_재시도하고_고갈되면_409() -> None:
    repository = OneConflictRepository()
    stack = build_employee_stack(repository)
    employee = stack.create_employee()
    repository.fail_next_replace = True

    observation = _observation(stack, employee.id, person_present=True)

    assert observation.resulting_status == EmployeeStatus.WORKING

    class AlwaysConflictRepository(OneConflictRepository):
        def replace_employee(
            self,
            employee: Employee,
            *,
            expected_version: int,
            history: EmployeeStatusHistory | None,
        ) -> Employee | None:
            return None

    blocked = build_employee_stack(AlwaysConflictRepository())
    blocked_employee = blocked.create_employee()
    with pytest.raises(EmployeeConcurrentUpdateError):
        _observation(blocked, blocked_employee.id, person_present=True)


def test_override_권한은_연결된_STAFF_본인만_허용(employee_stack: EmployeeStack) -> None:
    employee = employee_stack.create_employee(user_id=employee_stack.staff.id)
    other_staff = employee_stack.auth.seed(UserRole.STAFF, email="other-staff@example.invalid")
    command = SetStatusOverrideCommand(
        employee_id=employee.id,
        status=EmployeeStatus.AWAY,
        reason="업무 자리 비움",
        ends_at=None,
        expected_version=employee.version,
        operation_id=str(uuid4()),
    )

    with pytest.raises(PermissionDeniedError):
        employee_stack.service.set_status_override(
            other_staff, command, ip_fingerprint="test-ip-fingerprint"
        )
    with pytest.raises(PermissionDeniedError):
        employee_stack.service.set_status_override(
            employee_stack.student, command, ip_fingerprint="test-ip-fingerprint"
        )

    own = employee_stack.service.set_status_override(
        employee_stack.staff, command, ip_fingerprint="test-ip-fingerprint"
    )
    with pytest.raises(PermissionDeniedError):
        employee_stack.service.set_status_override(
            employee_stack.admin,
            SetStatusOverrideCommand(
                employee_id=employee.id,
                status=EmployeeStatus.OFFSITE,
                reason=None,
                ends_at=None,
                expected_version=own.version,
                operation_id=str(uuid4()),
            ),
            ip_fingerprint="test-ip-fingerprint",
        )


def test_네가지_수동상태와_같은상태_noop을_지원한다(
    employee_stack: EmployeeStack,
) -> None:
    employee = employee_stack.create_employee(user_id=employee_stack.staff.id)
    unchanged = employee_stack.service.set_status_override(
        employee_stack.staff,
        SetStatusOverrideCommand(
            employee.id,
            EmployeeStatus.AWAY,
            None,
            None,
            employee.version,
            str(uuid4()),
        ),
        ip_fingerprint="test",
    )
    assert unchanged == employee

    current = unchanged
    for selected_status in (
        EmployeeStatus.WORKING,
        EmployeeStatus.ON_CALL,
        EmployeeStatus.OFFSITE,
        EmployeeStatus.AWAY,
    ):
        current = employee_stack.service.set_status_override(
            employee_stack.staff,
            SetStatusOverrideCommand(
                current.id,
                selected_status,
                None,
                None,
                current.version,
                str(uuid4()),
            ),
            ip_fingerprint="test",
        )
        assert current.current_status.status == selected_status
        assert current.active_override is not None
        assert current.active_override.reason == "사용자 직접 설정"
