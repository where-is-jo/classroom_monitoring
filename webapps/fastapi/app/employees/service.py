"""직원 프로필, 상태 전이, override와 시간 정책 서비스."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

from ..audit.service import AuditService
from ..auth.errors import PermissionDeniedError
from ..users.models import ADMIN_ROLES, User, UserRole, UserStatus
from ..users.ports import UserRepository
from .errors import (
    EmployeeConcurrentUpdateError,
    EmployeeInactiveError,
    EmployeeNotFoundError,
    EmployeeOperationConflictError,
    EmployeeUserLinkConflictError,
    InvalidEmployeeProfileError,
    InvalidEmployeeUserError,
    InvalidStatusOverrideError,
)
from .models import (
    MANUAL_OVERRIDE_STATUSES,
    ClearStatusOverrideCommand,
    CreateEmployeeCommand,
    Employee,
    EmployeeCurrentStatus,
    EmployeeMutationResult,
    EmployeeObservation,
    EmployeeObservationResult,
    EmployeeOverride,
    EmployeePage,
    EmployeeStatus,
    EmployeeStatusEvaluation,
    EmployeeStatusHistory,
    EmployeeStatusHistoryPage,
    EmployeeStatusTransition,
    EvaluateEmployeeStatusesCommand,
    RecordEmployeeObservationCommand,
    SetStatusOverrideCommand,
    StatusSource,
    UpdateEmployeeCommand,
)
from .ports import EmployeeRepository


class EmployeeService:
    def __init__(
        self,
        repository: EmployeeRepository,
        user_repository: UserRepository,
        audit_service: AuditService,
        *,
        away_after_seconds: int,
        offsite_after_seconds: int,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._users = user_repository
        self._audit = audit_service
        self._away_after = timedelta(seconds=away_after_seconds)
        self._offsite_after = timedelta(seconds=offsite_after_seconds)
        self._clock = clock

    def list_employees(
        self,
        actor: User,
        *,
        limit: int,
        offset: int,
        search: str | None = None,
        department: str | None = None,
        status: EmployeeStatus | None = None,
        is_active: bool | None = None,
    ) -> EmployeePage:
        self._require_authenticated(actor)
        return self._repository.list_employees(
            limit=limit,
            offset=offset,
            search=search,
            department=department,
            status=status,
            is_active=is_active,
        )

    def get_employee(self, actor: User, employee_id: str) -> Employee:
        self._require_authenticated(actor)
        return self._get_required_employee(employee_id)

    def list_status_history(
        self,
        actor: User,
        employee_id: str,
        *,
        limit: int,
        offset: int,
        source: StatusSource | None = None,
        from_status: EmployeeStatus | None = None,
        to_status: EmployeeStatus | None = None,
    ) -> EmployeeStatusHistoryPage:
        self._require_authenticated(actor)
        self._get_required_employee(employee_id)
        return self._repository.list_status_history(
            employee_id,
            limit=limit,
            offset=offset,
            source=source,
            from_status=from_status,
            to_status=to_status,
        )

    def create_employee(
        self,
        actor: User,
        command: CreateEmployeeCommand,
        *,
        ip_fingerprint: str | None,
    ) -> Employee:
        self._require_admin(actor)
        existing = self._repository.get_employee_by_operation_id(command.operation_id)
        if existing is not None:
            if existing.employee_no != self._normalize_employee_no(command.employee_no):
                raise EmployeeOperationConflictError()
            return existing
        user_id = self._validate_staff_link(command.user_id)
        now = self._clock()
        employee = Employee(
            id=command.entity_id or str(uuid4()),
            employee_no=self._normalize_employee_no(command.employee_no),
            user_id=user_id,
            display_name=self._required_text(command.display_name),
            department=self._required_text(command.department),
            position=self._required_text(command.position),
            office_zone=self._required_text(command.office_zone),
            is_active=True,
            current_status=EmployeeCurrentStatus(
                status=EmployeeStatus.AWAY,
                source=StatusSource.SYSTEM,
                reason="직원 프로필 생성",
                effective_at=now,
                last_person_seen_at=None,
            ),
            active_override=None,
            created_at=now,
            updated_at=now,
            version=0,
            created_operation_id=command.operation_id,
            last_operation_id=command.operation_id,
            operation_ids=(command.operation_id,),
        )
        history = self._history(
            employee=employee,
            from_status=None,
            to_status=EmployeeStatus.AWAY,
            source=StatusSource.SYSTEM,
            reason="직원 프로필 생성",
            actor_user_id=actor.id,
            operation_id=command.operation_id,
            occurred_at=now,
        )
        saved = self._repository.create_employee(employee, history)
        self._record_audit(
            operation_id=command.operation_id,
            actor=actor,
            action="EMPLOYEE_CREATED",
            before=None,
            after=saved,
            ip_fingerprint=ip_fingerprint,
        )
        return saved

    def update_employee(
        self,
        actor: User,
        command: UpdateEmployeeCommand,
        *,
        ip_fingerprint: str | None,
    ) -> Employee:
        self._require_admin(actor)
        idempotent = self._idempotent_employee(command.operation_id, command.employee_id)
        if idempotent is not None:
            return idempotent
        current = self._get_required_employee(command.employee_id)
        if current.version != command.expected_version:
            raise EmployeeConcurrentUpdateError()
        user_id = current.user_id
        if command.change_user_link:
            user_id = self._validate_staff_link(
                command.user_id,
                employee_id=current.id,
            )
        updated = replace(
            current,
            employee_no=(
                current.employee_no
                if command.employee_no is None
                else self._normalize_employee_no(command.employee_no)
            ),
            user_id=user_id,
            display_name=self._optional_text(command.display_name, current.display_name),
            department=self._optional_text(command.department, current.department),
            position=self._optional_text(command.position, current.position),
            office_zone=self._optional_text(command.office_zone, current.office_zone),
            is_active=(current.is_active if command.is_active is None else command.is_active),
            active_override=(None if command.is_active is False else current.active_override),
            updated_at=self._clock(),
            version=current.version + 1,
            last_operation_id=command.operation_id,
            operation_ids=self._append_operation(current.operation_ids, command.operation_id),
        )
        saved = self._repository.replace_employee(
            updated,
            expected_version=current.version,
            history=None,
        )
        if saved is None:
            raise EmployeeConcurrentUpdateError()
        self._record_audit(
            operation_id=command.operation_id,
            actor=actor,
            action="EMPLOYEE_UPDATED",
            before=current,
            after=saved,
            ip_fingerprint=ip_fingerprint,
        )
        return saved

    def deactivate_employee(
        self,
        actor: User,
        employee_id: str,
        *,
        expected_version: int,
        operation_id: str,
        ip_fingerprint: str | None,
    ) -> Employee:
        self._require_admin(actor)
        idempotent = self._idempotent_employee(operation_id, employee_id)
        if idempotent is not None:
            return idempotent
        current = self._get_required_employee(employee_id)
        if current.version != expected_version:
            raise EmployeeConcurrentUpdateError()
        if not current.is_active:
            return current
        inactive = replace(
            current,
            is_active=False,
            active_override=None,
            updated_at=self._clock(),
            version=current.version + 1,
            last_operation_id=operation_id,
            operation_ids=self._append_operation(current.operation_ids, operation_id),
        )
        saved = self._repository.replace_employee(
            inactive,
            expected_version=current.version,
            history=None,
        )
        if saved is None:
            raise EmployeeConcurrentUpdateError()
        self._record_audit(
            operation_id=operation_id,
            actor=actor,
            action="EMPLOYEE_DEACTIVATED",
            before=current,
            after=saved,
            ip_fingerprint=ip_fingerprint,
        )
        return saved

    def set_status_override(
        self,
        actor: User,
        command: SetStatusOverrideCommand,
        *,
        ip_fingerprint: str | None,
    ) -> Employee:
        current = self._get_required_employee(command.employee_id)
        self._require_override_permission(actor, current)
        idempotent = self._idempotent_employee(command.operation_id, command.employee_id)
        if idempotent is not None:
            return idempotent
        self._require_active(current)
        if current.version != command.expected_version:
            raise EmployeeConcurrentUpdateError()
        if current.current_status.status == command.status:
            return current
        now = self._clock()
        reason = (
            "사용자 직접 설정" if command.reason is None else self._required_text(command.reason)
        )
        invalid_end = command.ends_at is not None and (
            command.ends_at.tzinfo is None or command.ends_at <= now
        )
        if command.status not in MANUAL_OVERRIDE_STATUSES or invalid_end:
            raise InvalidStatusOverrideError()
        current_status = EmployeeCurrentStatus(
            status=command.status,
            source=StatusSource.MANUAL,
            reason=reason,
            effective_at=now,
            last_person_seen_at=current.current_status.last_person_seen_at,
        )
        updated = replace(
            current,
            current_status=current_status,
            active_override=EmployeeOverride(
                status=command.status,
                reason=reason,
                actor_user_id=actor.id,
                starts_at=now,
                ends_at=command.ends_at,
            ),
            updated_at=now,
            version=current.version + 1,
            last_operation_id=command.operation_id,
            operation_ids=self._append_operation(current.operation_ids, command.operation_id),
        )
        history = self._status_history_if_changed(
            current,
            updated,
            actor_user_id=actor.id,
            operation_id=command.operation_id,
        )
        saved = self._repository.replace_employee(
            updated,
            expected_version=current.version,
            history=history,
        )
        if saved is None:
            raise EmployeeConcurrentUpdateError()
        self._record_audit(
            operation_id=command.operation_id,
            actor=actor,
            action="EMPLOYEE_STATUS_OVERRIDE_SET",
            before=current,
            after=saved,
            ip_fingerprint=ip_fingerprint,
        )
        return saved

    def clear_status_override(
        self,
        actor: User,
        command: ClearStatusOverrideCommand,
        *,
        ip_fingerprint: str | None,
    ) -> Employee:
        current = self._get_required_employee(command.employee_id)
        self._require_override_permission(actor, current)
        idempotent = self._idempotent_employee(command.operation_id, command.employee_id)
        if idempotent is not None:
            return idempotent
        self._require_active(current)
        if current.version != command.expected_version:
            raise EmployeeConcurrentUpdateError()
        if current.active_override is None:
            return current
        now = self._clock()
        recalculated = self._status_after_override(current, now=now)
        updated = replace(
            current,
            current_status=recalculated,
            active_override=None,
            updated_at=now,
            version=current.version + 1,
            last_operation_id=command.operation_id,
            operation_ids=self._append_operation(current.operation_ids, command.operation_id),
        )
        history = self._status_history_if_changed(
            current,
            updated,
            actor_user_id=actor.id,
            operation_id=command.operation_id,
        )
        saved = self._repository.replace_employee(
            updated,
            expected_version=current.version,
            history=history,
        )
        if saved is None:
            raise EmployeeConcurrentUpdateError()
        self._record_audit(
            operation_id=command.operation_id,
            actor=actor,
            action="EMPLOYEE_STATUS_OVERRIDE_CLEARED",
            before=current,
            after=saved,
            ip_fingerprint=ip_fingerprint,
        )
        return saved

    def evaluate_statuses(
        self,
        actor: User,
        command: EvaluateEmployeeStatusesCommand,
        *,
        ip_fingerprint: str | None,
    ) -> EmployeeStatusEvaluation:
        self._require_admin(actor)
        now = self._clock()
        employees = self._repository.list_active_employees()
        changed_count = 0
        for employee in employees:
            operation_id = str(
                uuid5(NAMESPACE_URL, f"employee-evaluation:{command.operation_id}:{employee.id}")
            )
            changed_count += int(
                self._evaluate_one(
                    actor,
                    employee,
                    operation_id=operation_id,
                    now=now,
                    ip_fingerprint=ip_fingerprint,
                )
            )
        return EmployeeStatusEvaluation(
            evaluated_at=now,
            evaluated_count=len(employees),
            changed_count=changed_count,
        )

    def record_mock_observation(
        self,
        actor: User,
        command: RecordEmployeeObservationCommand,
    ) -> EmployeeObservation:
        self._require_admin(actor)
        existing = self._repository.get_observation(command.event_id)
        if existing is not None:
            return existing
        if command.observed_at.tzinfo is None or not 0 <= command.confidence <= 1:
            raise InvalidEmployeeProfileError()

        for _ in range(3):
            employee = self._get_required_employee(command.employee_id)
            self._require_active(employee)
            existing = self._repository.get_observation(command.event_id)
            if existing is not None:
                return existing
            latest = self._repository.get_latest_observation(employee.id)
            if latest is not None and command.observed_at < latest.observed_at:
                return self._store_observation(
                    command,
                    resulting_status=employee.current_status.status,
                    status_changed=False,
                )

            received_at = self._clock()
            updated = self._employee_after_observation(
                employee,
                command,
                received_at=received_at,
            )
            if updated == employee:
                return self._store_observation(
                    command,
                    resulting_status=employee.current_status.status,
                    status_changed=False,
                    received_at=received_at,
                )
            history = self._status_history_if_changed(
                employee,
                updated,
                actor_user_id=actor.id,
                operation_id=command.event_id,
            )
            saved = self._repository.replace_employee(
                updated,
                expected_version=employee.version,
                history=history,
            )
            if saved is None:
                continue
            return self._store_observation(
                command,
                resulting_status=saved.current_status.status,
                status_changed=history is not None,
                received_at=received_at,
            )
        raise EmployeeConcurrentUpdateError()

    def record_mock_observation_result(
        self,
        actor: User,
        command: RecordEmployeeObservationCommand,
    ) -> EmployeeObservationResult:
        observation = self.record_mock_observation(actor, command)
        return EmployeeObservationResult(
            observation=observation,
            transition=self._transition_for_operation(
                command.employee_id,
                command.event_id,
                fallback_status=observation.resulting_status,
            ),
        )

    def clear_status_override_result(
        self,
        actor: User,
        command: ClearStatusOverrideCommand,
        *,
        ip_fingerprint: str | None,
    ) -> EmployeeMutationResult:
        employee = self.clear_status_override(
            actor,
            command,
            ip_fingerprint=ip_fingerprint,
        )
        return EmployeeMutationResult(
            employee=employee,
            transition=self._transition_for_operation(
                command.employee_id,
                command.operation_id,
                fallback_status=employee.current_status.status,
            ),
        )

    def set_status_override_result(
        self,
        actor: User,
        command: SetStatusOverrideCommand,
        *,
        ip_fingerprint: str | None,
    ) -> EmployeeMutationResult:
        employee = self.set_status_override(
            actor,
            command,
            ip_fingerprint=ip_fingerprint,
        )
        return EmployeeMutationResult(
            employee=employee,
            transition=self._transition_for_operation(
                command.employee_id,
                command.operation_id,
                fallback_status=employee.current_status.status,
            ),
        )

    def can_override(self, actor: User, employee: Employee) -> bool:
        return (
            actor.status == UserStatus.ACTIVE
            and actor.role == UserRole.STAFF
            and employee.user_id == actor.id
        )

    def get_linked_employee(self, actor: User) -> Employee | None:
        if actor.status != UserStatus.ACTIVE or actor.role != UserRole.STAFF:
            return None
        return self._repository.get_employee_by_user_id(actor.id)

    def _evaluate_one(
        self,
        actor: User,
        employee: Employee,
        *,
        operation_id: str,
        now: datetime,
        ip_fingerprint: str | None,
    ) -> bool:
        existing_history = self._repository.get_history_by_operation_id(operation_id)
        if existing_history is not None:
            return True
        for _ in range(3):
            current = self._repository.get_employee(employee.id)
            if current is None or not current.is_active:
                return False
            override_expired = (
                current.active_override is not None
                and current.active_override.ends_at is not None
                and current.active_override.ends_at <= now
            )
            if current.active_override is not None and not override_expired:
                return False
            candidate = (
                self._status_after_override(current, now=now)
                if override_expired
                else self._time_policy_status(current, now=now)
            )
            if candidate is None:
                return False
            if candidate.status == current.current_status.status and not override_expired:
                return False
            updated = replace(
                current,
                current_status=candidate,
                active_override=None if override_expired else current.active_override,
                updated_at=now,
                version=current.version + 1,
                last_operation_id=operation_id,
                operation_ids=self._append_operation(current.operation_ids, operation_id),
            )
            history = self._status_history_if_changed(
                current,
                updated,
                actor_user_id=actor.id,
                operation_id=operation_id,
            )
            saved = self._repository.replace_employee(
                updated,
                expected_version=current.version,
                history=history,
            )
            if saved is None:
                continue
            if override_expired:
                self._record_audit(
                    operation_id=operation_id,
                    actor=actor,
                    action="EMPLOYEE_STATUS_OVERRIDE_EXPIRED",
                    before=current,
                    after=saved,
                    ip_fingerprint=ip_fingerprint,
                )
            return history is not None
        raise EmployeeConcurrentUpdateError()

    def _employee_after_observation(
        self,
        employee: Employee,
        command: RecordEmployeeObservationCommand,
        *,
        received_at: datetime,
    ) -> Employee:
        active_override = employee.active_override
        override_is_active = active_override is not None and (
            active_override.ends_at is None or active_override.ends_at > received_at
        )
        base_employee = employee
        if active_override is not None and not override_is_active:
            base_employee = replace(
                employee,
                current_status=self._status_after_override(
                    employee,
                    now=received_at,
                ),
                active_override=None,
            )
            active_override = None
        last_person_seen_at = base_employee.current_status.last_person_seen_at
        if command.person_present:
            last_person_seen_at = command.observed_at

        if override_is_active:
            if last_person_seen_at == employee.current_status.last_person_seen_at:
                return employee
            current_status = replace(
                base_employee.current_status,
                last_person_seen_at=last_person_seen_at,
            )
        elif command.person_present:
            status = EmployeeStatus.ON_CALL if command.phone_detected else EmployeeStatus.WORKING
            current_status = EmployeeCurrentStatus(
                status=status,
                source=StatusSource.MOCK,
                reason=(
                    "mock 관측: 사람 있음, 통화 중"
                    if command.phone_detected
                    else "mock 관측: 사람 있음, 통화 없음"
                ),
                effective_at=command.observed_at,
                last_person_seen_at=last_person_seen_at,
            )
            active_override = None
        else:
            base = replace(
                base_employee,
                active_override=None,
                current_status=replace(
                    base_employee.current_status,
                    last_person_seen_at=last_person_seen_at,
                ),
            )
            evaluated_status = self._time_policy_status(base, now=received_at)
            current_status = base.current_status if evaluated_status is None else evaluated_status
            active_override = None

        return replace(
            base_employee,
            current_status=current_status,
            active_override=active_override,
            updated_at=received_at,
            version=employee.version + 1,
            last_operation_id=command.event_id,
            operation_ids=self._append_operation(employee.operation_ids, command.event_id),
        )

    @staticmethod
    def _append_operation(operation_ids: tuple[str, ...], operation_id: str) -> tuple[str, ...]:
        if operation_id in operation_ids:
            return operation_ids
        return (*operation_ids, operation_id)

    def _status_after_override(
        self,
        employee: Employee,
        *,
        now: datetime,
    ) -> EmployeeCurrentStatus:
        latest_present = self._repository.get_latest_present_observation(employee.id)
        if latest_present is None:
            return EmployeeCurrentStatus(
                status=EmployeeStatus.AWAY,
                source=StatusSource.SYSTEM,
                reason="유효한 사람 있음 관측 없음",
                effective_at=now,
                last_person_seen_at=None,
            )
        elapsed = now - latest_present.observed_at
        if elapsed >= self._offsite_after:
            status = EmployeeStatus.OFFSITE
            source = StatusSource.TIME_POLICY
            reason = "마지막 사람 있음 관측 후 외근 기준 경과"
        elif elapsed >= self._away_after:
            status = EmployeeStatus.AWAY
            source = StatusSource.TIME_POLICY
            reason = "마지막 사람 있음 관측 후 부재 기준 경과"
        else:
            status = (
                EmployeeStatus.ON_CALL if latest_present.phone_detected else EmployeeStatus.WORKING
            )
            source = StatusSource.MOCK
            reason = "최신 유효 mock 관측 재적용"
        return EmployeeCurrentStatus(
            status=status,
            source=source,
            reason=reason,
            effective_at=now,
            last_person_seen_at=latest_present.observed_at,
        )

    def _time_policy_status(
        self,
        employee: Employee,
        *,
        now: datetime,
    ) -> EmployeeCurrentStatus | None:
        last_seen = employee.current_status.last_person_seen_at
        if last_seen is None:
            return None
        elapsed = now - last_seen
        if elapsed >= self._offsite_after:
            status = EmployeeStatus.OFFSITE
            reason = "마지막 사람 있음 관측 후 외근 기준 경과"
        elif elapsed >= self._away_after:
            status = EmployeeStatus.AWAY
            reason = "마지막 사람 있음 관측 후 부재 기준 경과"
        else:
            return None
        return EmployeeCurrentStatus(
            status=status,
            source=StatusSource.TIME_POLICY,
            reason=reason,
            effective_at=now,
            last_person_seen_at=last_seen,
        )

    def _store_observation(
        self,
        command: RecordEmployeeObservationCommand,
        *,
        resulting_status: EmployeeStatus,
        status_changed: bool,
        received_at: datetime | None = None,
    ) -> EmployeeObservation:
        observation = EmployeeObservation(
            event_id=command.event_id,
            employee_id=command.employee_id,
            person_present=command.person_present,
            phone_detected=command.phone_detected,
            confidence=command.confidence,
            observed_at=command.observed_at,
            received_at=received_at or self._clock(),
            resulting_status=resulting_status,
            status_changed=status_changed,
        )
        return self._repository.create_observation(observation)

    def _status_history_if_changed(
        self,
        before: Employee,
        after: Employee,
        *,
        actor_user_id: str | None,
        operation_id: str,
    ) -> EmployeeStatusHistory | None:
        if before.current_status.status == after.current_status.status:
            return None
        return self._history(
            employee=after,
            from_status=before.current_status.status,
            to_status=after.current_status.status,
            source=after.current_status.source,
            reason=after.current_status.reason,
            actor_user_id=actor_user_id,
            operation_id=operation_id,
            occurred_at=after.current_status.effective_at,
        )

    @staticmethod
    def _history(
        *,
        employee: Employee,
        from_status: EmployeeStatus | None,
        to_status: EmployeeStatus,
        source: StatusSource,
        reason: str,
        actor_user_id: str | None,
        operation_id: str,
        occurred_at: datetime,
    ) -> EmployeeStatusHistory:
        return EmployeeStatusHistory(
            id=str(uuid4()),
            employee_id=employee.id,
            from_status=from_status,
            to_status=to_status,
            source=source,
            reason=reason,
            actor_user_id=actor_user_id,
            operation_id=operation_id,
            occurred_at=occurred_at,
        )

    def _idempotent_employee(
        self,
        operation_id: str,
        employee_id: str,
    ) -> Employee | None:
        employee = self._repository.get_employee_by_operation_id(operation_id)
        if employee is None:
            history = self._repository.get_history_by_operation_id(operation_id)
            if history is None:
                return None
            if history.employee_id != employee_id:
                raise EmployeeOperationConflictError()
            return self._get_required_employee(employee_id)
        if employee.id != employee_id:
            raise EmployeeOperationConflictError()
        return employee

    def _transition_for_operation(
        self,
        employee_id: str,
        operation_id: str,
        *,
        fallback_status: EmployeeStatus,
    ) -> EmployeeStatusTransition:
        history = self._repository.get_history_by_operation_id(operation_id)
        if history is None:
            return EmployeeStatusTransition(
                employee_id=employee_id,
                from_status=fallback_status,
                to_status=fallback_status,
                status_changed=False,
            )
        if history.employee_id != employee_id or history.from_status is None:
            raise EmployeeOperationConflictError()
        return EmployeeStatusTransition(
            employee_id=employee_id,
            from_status=history.from_status,
            to_status=history.to_status,
            status_changed=history.from_status != history.to_status,
        )

    @staticmethod
    def is_present(employee: Employee) -> bool:
        return employee.current_status.status in {
            EmployeeStatus.WORKING,
            EmployeeStatus.ON_CALL,
        }

    def _validate_staff_link(
        self,
        user_id: str | None,
        *,
        employee_id: str | None = None,
    ) -> str | None:
        if user_id is None:
            return None
        user = self._users.get_user(user_id)
        if user is None or user.role != UserRole.STAFF:
            raise InvalidEmployeeUserError()
        linked = self._repository.get_employee_by_user_id(user_id)
        if linked is not None and linked.id != employee_id:
            raise EmployeeUserLinkConflictError()
        return user_id

    def _get_required_employee(self, employee_id: str) -> Employee:
        employee = self._repository.get_employee(employee_id)
        if employee is None:
            raise EmployeeNotFoundError()
        return employee

    @staticmethod
    def _require_authenticated(actor: User) -> None:
        if actor.status != UserStatus.ACTIVE:
            raise PermissionDeniedError()

    @staticmethod
    def _require_admin(actor: User) -> None:
        if actor.status != UserStatus.ACTIVE or actor.role not in ADMIN_ROLES:
            raise PermissionDeniedError()

    def _require_override_permission(self, actor: User, employee: Employee) -> None:
        if not self.can_override(actor, employee):
            raise PermissionDeniedError()

    @staticmethod
    def _require_active(employee: Employee) -> None:
        if not employee.is_active:
            raise EmployeeInactiveError()

    @staticmethod
    def _required_text(value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise InvalidEmployeeProfileError()
        return normalized

    def _optional_text(self, value: str | None, current: str) -> str:
        return current if value is None else self._required_text(value)

    def _normalize_employee_no(self, value: str) -> str:
        return self._required_text(value).upper()

    def _record_audit(
        self,
        *,
        operation_id: str,
        actor: User,
        action: str,
        before: Employee | None,
        after: Employee,
        ip_fingerprint: str | None,
    ) -> None:
        self._audit.record(
            operation_id=operation_id,
            actor_user_id=actor.id,
            action=action,
            resource_type="employee",
            resource_id=after.id,
            before=_employee_audit_state(before),
            after=_employee_audit_state(after),
            ip_fingerprint=ip_fingerprint,
        )


def _employee_audit_state(employee: Employee | None) -> dict[str, object]:
    if employee is None:
        return {}
    return {
        "user_id": employee.user_id,
        "is_active": employee.is_active,
        "status": employee.current_status.status.value,
        "status_source": employee.current_status.source.value,
        "has_active_override": employee.active_override is not None,
    }
