"""FastAPI와 저장 기술에 의존하지 않는 면담 대기 규칙과 같은-process 조정."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

from ..auth.errors import PermissionDeniedError
from ..employees.models import (
    ClearStatusOverrideCommand,
    Employee,
    EmployeeObservation,
    EmployeeStatus,
    EmployeeStatusTransition,
    RecordEmployeeObservationCommand,
    SetStatusOverrideCommand,
)
from ..employees.ports import EmployeeRepository
from ..employees.service import EmployeeService
from ..notifications.models import CreateNotificationCommand
from ..notifications.service import NotificationService
from ..users.models import ADMIN_ROLES, User, UserRole, UserStatus
from ..users.ports import UserRepository
from .errors import (
    InterviewWaitConcurrentUpdateError,
    InterviewWaitDuplicateError,
    InterviewWaitInputError,
    InterviewWaitNotFoundError,
    InterviewWaitOperationConflictError,
    InterviewWaitTransitionError,
)
from .models import (
    ACTIVE_WAIT_STATUSES,
    CreateInterviewWaitCommand,
    EvaluateInterviewWaitExpirationsCommand,
    InterviewWait,
    InterviewWaitDisplay,
    InterviewWaitExpirationResult,
    InterviewWaitHistory,
    InterviewWaitPage,
    InterviewWaitStatus,
    TransitionInterviewWaitCommand,
)
from .ports import InterviewWaitRepository

_PRESENT_STATUSES = frozenset({EmployeeStatus.WORKING, EmployeeStatus.ON_CALL})
_ABSENT_STATUSES = frozenset({EmployeeStatus.AWAY, EmployeeStatus.OFFSITE})


class InterviewWaitService:
    def __init__(
        self,
        repository: InterviewWaitRepository,
        employee_repository: EmployeeRepository,
        user_repository: UserRepository,
        notification_service: NotificationService,
        *,
        expires_after_hours: int,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._employees = employee_repository
        self._users = user_repository
        self._notifications = notification_service
        self._expires_after = timedelta(hours=expires_after_hours)
        self._clock = clock

    def create_wait(self, actor: User, command: CreateInterviewWaitCommand) -> InterviewWait:
        self._require_active_user(actor)
        self._require_student(actor)
        employee = self._required_active_employee(command.employee_id)
        message = self._normalize_message(command.message)
        operation_id = self._required_operation_id(command.operation_id)
        existing_operation = self._repository.get_wait_by_operation_id(operation_id)
        if existing_operation is not None:
            if (
                existing_operation.requester_user_id != actor.id
                or existing_operation.employee_id != employee.id
                or existing_operation.message != message
            ):
                raise InterviewWaitOperationConflictError()
            self._ensure_history(existing_operation, operation_id)
            self._ensure_ready_notification(existing_operation)
            return existing_operation
        if self._repository.get_active_wait(actor.id, employee.id) is not None:
            raise InterviewWaitDuplicateError()

        now = self._clock()
        initial_status = (
            InterviewWaitStatus.READY
            if employee.current_status.status in _PRESENT_STATUSES
            else InterviewWaitStatus.WAITING
        )
        wait = InterviewWait(
            id=str(uuid4()),
            requester_user_id=actor.id,
            employee_id=employee.id,
            status=initial_status,
            message=message,
            requested_at=now,
            ready_at=now if initial_status == InterviewWaitStatus.READY else None,
            completed_at=None,
            cancelled_at=None,
            expires_at=now + self._expires_after,
            version=0,
            active_key=self._active_key(actor.id, employee.id),
            created_operation_id=operation_id,
            last_operation_id=operation_id,
            operation_ids=(operation_id,),
            last_actor_user_id=actor.id,
        )
        history = self._history(
            wait=wait,
            from_status=None,
            to_status=initial_status,
            reason=(
                "직원이 이미 재석 중이어서 즉시 준비됨"
                if initial_status == InterviewWaitStatus.READY
                else "면담 대기 등록"
            ),
            actor_user_id=actor.id,
            operation_id=operation_id,
            occurred_at=now,
        )
        stored = self._repository.create_wait(wait, history)
        if stored.id != wait.id:
            if stored.created_operation_id != operation_id:
                raise InterviewWaitDuplicateError()
            self._ensure_history(stored, operation_id)
        self._ensure_ready_notification(stored)
        return stored

    def list_waits(
        self,
        actor: User,
        *,
        status: InterviewWaitStatus | None,
        employee_id: str | None,
        limit: int,
        offset: int,
    ) -> InterviewWaitPage:
        self._require_active_user(actor)
        requester_filter: str | None = None
        employee_filter = employee_id
        if actor.role == UserRole.STAFF:
            linked = self._employees.get_employee_by_user_id(actor.id)
            if linked is None:
                return InterviewWaitPage(items=[], total=0)
            if employee_id is not None and employee_id != linked.id:
                raise PermissionDeniedError()
            employee_filter = linked.id
        elif actor.role == UserRole.STUDENT:
            requester_filter = actor.id
        else:
            raise PermissionDeniedError()
        return self._repository.list_waits(
            requester_user_id=requester_filter,
            employee_id=employee_filter,
            status=status,
            limit=limit,
            offset=offset,
        )

    def list_requester_waits(
        self,
        actor: User,
        *,
        status: InterviewWaitStatus | None,
        limit: int,
        offset: int,
    ) -> InterviewWaitPage:
        self._require_active_user(actor)
        self._require_student(actor)
        return self._repository.list_waits(
            requester_user_id=actor.id,
            employee_id=None,
            status=status,
            limit=limit,
            offset=offset,
        )

    def list_staff_waits(
        self,
        actor: User,
        *,
        status: InterviewWaitStatus | None,
        limit: int,
        offset: int,
    ) -> InterviewWaitPage:
        self._require_active_user(actor)
        if actor.role != UserRole.STAFF:
            raise PermissionDeniedError()
        linked = self._employees.get_employee_by_user_id(actor.id)
        if linked is None:
            return InterviewWaitPage(items=[], total=0)
        return self._repository.list_waits(
            requester_user_id=None,
            employee_id=linked.id,
            status=status,
            limit=limit,
            offset=offset,
        )

    def get_wait(self, actor: User, wait_id: str) -> InterviewWait:
        self._require_active_user(actor)
        wait = self._required_wait(wait_id)
        self._require_view_permission(actor, wait)
        return wait

    def display(self, wait: InterviewWait) -> InterviewWaitDisplay:
        employee = self._employees.get_employee(wait.employee_id)
        requester = self._users.get_user(wait.requester_user_id)
        return InterviewWaitDisplay(
            wait=wait,
            employee_name=(employee.display_name if employee else "알 수 없는 직원"),
            requester_name=(requester.name if requester else "알 수 없는 요청자"),
        )

    def transition_wait(
        self, actor: User, command: TransitionInterviewWaitCommand
    ) -> InterviewWait:
        self._require_active_user(actor)
        operation_id = self._required_operation_id(command.operation_id)
        operation_owner = self._repository.get_wait_by_operation_id(operation_id)
        if operation_owner is not None:
            if operation_owner.id != command.wait_id or operation_owner.status != command.status:
                raise InterviewWaitOperationConflictError()
            self._ensure_history(operation_owner, operation_id)
            return operation_owner

        wait = self._required_wait(command.wait_id)
        wait = self._expire_if_due(
            wait,
            operation_id=f"{operation_id}:expiration",
            actor_user_id=actor.id,
        )
        if command.status == InterviewWaitStatus.CANCELLED:
            self._require_cancel_permission(actor, wait)
            if wait.status == command.status:
                return wait
            if wait.status not in ACTIVE_WAIT_STATUSES:
                raise InterviewWaitTransitionError()
            return self._transition(
                wait,
                to_status=InterviewWaitStatus.CANCELLED,
                operation_id=operation_id,
                actor_user_id=actor.id,
                reason="요청자 또는 관리자가 면담 대기를 취소함",
                occurred_at=self._clock(),
            )
        if command.status == InterviewWaitStatus.COMPLETED:
            self._require_complete_permission(actor, wait)
            if wait.status == command.status:
                return wait
            if wait.status != InterviewWaitStatus.READY:
                raise InterviewWaitTransitionError()
            return self._transition(
                wait,
                to_status=InterviewWaitStatus.COMPLETED,
                operation_id=operation_id,
                actor_user_id=actor.id,
                reason="면담 완료 처리",
                occurred_at=self._clock(),
            )
        raise InterviewWaitTransitionError()

    def list_history(self, actor: User, wait_id: str) -> list[InterviewWaitHistory]:
        wait = self.get_wait(actor, wait_id)
        return self._repository.list_history(wait.id)

    def can_cancel(self, actor: User, wait: InterviewWait) -> bool:
        return (
            actor.role == UserRole.STUDENT
            and wait.status in ACTIVE_WAIT_STATUSES
            and wait.requester_user_id == actor.id
        )

    def can_complete(self, actor: User, wait: InterviewWait) -> bool:
        if wait.status != InterviewWaitStatus.READY:
            return False
        if actor.role == UserRole.STUDENT and wait.requester_user_id == actor.id:
            return True
        if actor.role != UserRole.STAFF:
            return False
        employee = self._employees.get_employee_by_user_id(actor.id)
        return employee is not None and employee.id == wait.employee_id

    def evaluate_expirations(
        self,
        actor: User,
        command: EvaluateInterviewWaitExpirationsCommand,
    ) -> InterviewWaitExpirationResult:
        self._require_admin(actor)
        evaluation_operation_id = self._required_operation_id(command.operation_id)
        now = self._clock()
        candidates = self._repository.list_expired_candidates(now)
        expired_count = 0
        for wait in candidates:
            operation_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"interview-wait-expiration:{evaluation_operation_id}:{wait.id}",
                )
            )
            current = self._repository.get_wait(wait.id)
            if current is None or current.status not in ACTIVE_WAIT_STATUSES:
                continue
            self._transition(
                current,
                to_status=InterviewWaitStatus.EXPIRED,
                operation_id=operation_id,
                actor_user_id=actor.id,
                reason="면담 대기 만료 시각 경과",
                occurred_at=now,
            )
            expired_count += 1
        return InterviewWaitExpirationResult(
            evaluated_at=now,
            evaluated_count=len(candidates),
            expired_count=expired_count,
        )

    def mark_employee_ready(
        self,
        employee_id: str,
        *,
        source_operation_id: str,
        actor_user_id: str | None,
    ) -> int:
        now = self._clock()
        changed_count = 0
        for wait in self._repository.list_active_for_employee(employee_id):
            if wait.status == InterviewWaitStatus.READY:
                self._ensure_ready_notification(wait)
                continue
            if wait.expires_at <= now:
                expiration_operation = str(
                    uuid5(
                        NAMESPACE_URL,
                        f"interview-wait-expiration:{source_operation_id}:{wait.id}",
                    )
                )
                self._transition(
                    wait,
                    to_status=InterviewWaitStatus.EXPIRED,
                    operation_id=expiration_operation,
                    actor_user_id=actor_user_id,
                    reason="직원 복귀 처리 전에 면담 대기 만료",
                    occurred_at=now,
                )
                continue
            ready_operation = str(
                uuid5(
                    NAMESPACE_URL,
                    f"interview-wait-ready:{source_operation_id}:{wait.id}",
                )
            )
            ready = self._transition(
                wait,
                to_status=InterviewWaitStatus.READY,
                operation_id=ready_operation,
                actor_user_id=actor_user_id,
                reason="대상 직원이 부재 상태에서 재석 상태로 변경됨",
                occurred_at=now,
            )
            self._ensure_ready_notification(ready)
            changed_count += 1
        return changed_count

    def cancel_for_employee(
        self,
        employee_id: str,
        *,
        source_operation_id: str,
        actor_user_id: str,
    ) -> int:
        cancelled_count = 0
        for wait in self._repository.list_active_for_employee(employee_id):
            operation_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"interview-wait-employee-deactivated:{source_operation_id}:{wait.id}",
                )
            )
            self._transition(
                wait,
                to_status=InterviewWaitStatus.CANCELLED,
                operation_id=operation_id,
                actor_user_id=actor_user_id,
                reason="대상 직원 비활성화로 면담 대기 취소",
                occurred_at=self._clock(),
            )
            cancelled_count += 1
        return cancelled_count

    def _transition(
        self,
        wait: InterviewWait,
        *,
        to_status: InterviewWaitStatus,
        operation_id: str,
        actor_user_id: str | None,
        reason: str,
        occurred_at: datetime,
    ) -> InterviewWait:
        operation_owner = self._repository.get_wait_by_operation_id(operation_id)
        if operation_owner is not None:
            if operation_owner.id != wait.id or operation_owner.status != to_status:
                raise InterviewWaitOperationConflictError()
            self._ensure_history(operation_owner, operation_id)
            if to_status == InterviewWaitStatus.READY:
                self._ensure_ready_notification(operation_owner)
            return operation_owner
        for _ in range(3):
            current = self._required_wait(wait.id)
            if current.status == to_status:
                return current
            if not self._is_allowed_transition(current.status, to_status):
                raise InterviewWaitTransitionError()
            updated = replace(
                current,
                status=to_status,
                ready_at=(
                    occurred_at if to_status == InterviewWaitStatus.READY else current.ready_at
                ),
                completed_at=(
                    occurred_at
                    if to_status == InterviewWaitStatus.COMPLETED
                    else current.completed_at
                ),
                cancelled_at=(
                    occurred_at
                    if to_status == InterviewWaitStatus.CANCELLED
                    else current.cancelled_at
                ),
                version=current.version + 1,
                active_key=(current.active_key if to_status in ACTIVE_WAIT_STATUSES else None),
                last_operation_id=operation_id,
                operation_ids=self._append_operation(current.operation_ids, operation_id),
                last_actor_user_id=actor_user_id,
            )
            history = self._history(
                wait=updated,
                from_status=current.status,
                to_status=to_status,
                reason=reason,
                actor_user_id=actor_user_id,
                operation_id=operation_id,
                occurred_at=occurred_at,
            )
            saved = self._repository.replace_wait(
                updated,
                expected_version=current.version,
                history=history,
            )
            if saved is None:
                continue
            if to_status == InterviewWaitStatus.READY:
                self._ensure_ready_notification(saved)
            return saved
        raise InterviewWaitConcurrentUpdateError()

    def _expire_if_due(
        self,
        wait: InterviewWait,
        *,
        operation_id: str,
        actor_user_id: str | None,
    ) -> InterviewWait:
        now = self._clock()
        if wait.status not in ACTIVE_WAIT_STATUSES or wait.expires_at > now:
            return wait
        return self._transition(
            wait,
            to_status=InterviewWaitStatus.EXPIRED,
            operation_id=operation_id,
            actor_user_id=actor_user_id,
            reason="상태 변경 요청 전에 면담 대기 만료",
            occurred_at=now,
        )

    def _ensure_history(self, wait: InterviewWait, operation_id: str) -> None:
        if self._repository.get_history_by_operation_id(operation_id) is not None:
            return
        if operation_id == wait.created_operation_id:
            from_status = None
            occurred_at = wait.requested_at
            reason = (
                "직원이 이미 재석 중이어서 즉시 준비됨"
                if wait.status == InterviewWaitStatus.READY
                else "면담 대기 등록"
            )
        else:
            from_status = self._previous_status(wait)
            occurred_at = self._transition_time(wait)
            reason = self._transition_reason(wait.status)
        self._repository.append_history(
            self._history(
                wait=wait,
                from_status=from_status,
                to_status=wait.status,
                reason=reason,
                actor_user_id=wait.last_actor_user_id,
                operation_id=operation_id,
                occurred_at=occurred_at,
            )
        )

    def _ensure_ready_notification(self, wait: InterviewWait) -> None:
        if wait.status != InterviewWaitStatus.READY:
            return
        self._notifications.create(
            CreateNotificationCommand(
                recipient_user_id=wait.requester_user_id,
                type="INTERVIEW_WAIT_READY",
                title="면담 준비가 완료됐습니다",
                body="대상 직원이 재석 상태입니다. 면담 대기를 확인해 주세요.",
                data={
                    "target_route": f"/my/interview-waits/{wait.id}",
                    "interview_wait_id": wait.id,
                    "employee_id": wait.employee_id,
                },
                operation_id=f"interview-wait-ready-notification:{wait.id}",
                dedupe_key=f"interview_wait_ready:{wait.id}",
            )
        )

    def _require_view_permission(self, actor: User, wait: InterviewWait) -> None:
        if actor.role == UserRole.STUDENT and wait.requester_user_id == actor.id:
            return
        if actor.role == UserRole.STAFF:
            employee = self._employees.get_employee_by_user_id(actor.id)
            if employee is not None and employee.id == wait.employee_id:
                return
        raise PermissionDeniedError()

    def _require_cancel_permission(self, actor: User, wait: InterviewWait) -> None:
        if actor.role != UserRole.STUDENT or wait.requester_user_id != actor.id:
            raise PermissionDeniedError()

    def _require_complete_permission(self, actor: User, wait: InterviewWait) -> None:
        if actor.role == UserRole.STUDENT and wait.requester_user_id == actor.id:
            return
        if actor.role == UserRole.STAFF:
            employee = self._employees.get_employee_by_user_id(actor.id)
            if employee is not None and employee.id == wait.employee_id:
                return
        raise PermissionDeniedError()

    @staticmethod
    def _require_active_user(actor: User) -> None:
        if actor.status != UserStatus.ACTIVE:
            raise PermissionDeniedError()

    @staticmethod
    def _require_student(actor: User) -> None:
        if actor.role != UserRole.STUDENT:
            raise PermissionDeniedError()

    @staticmethod
    def _require_admin(actor: User) -> None:
        if actor.status != UserStatus.ACTIVE or actor.role not in ADMIN_ROLES:
            raise PermissionDeniedError()

    def _required_active_employee(self, employee_id: str) -> Employee:
        employee = self._employees.get_employee(employee_id)
        if employee is None or not employee.is_active:
            raise InterviewWaitInputError("활성 대상 직원을 찾을 수 없습니다.")
        return employee

    def _required_wait(self, wait_id: str) -> InterviewWait:
        wait = self._repository.get_wait(wait_id)
        if wait is None:
            raise InterviewWaitNotFoundError()
        return wait

    @staticmethod
    def _normalize_message(message: str | None) -> str | None:
        if message is None:
            return None
        normalized = message.strip()
        if not normalized:
            return None
        if len(normalized) > 500:
            raise InterviewWaitInputError("면담 요청 메시지는 500자 이하여야 합니다.")
        return normalized

    @staticmethod
    def _required_operation_id(operation_id: str) -> str:
        normalized = operation_id.strip()
        if not normalized or len(normalized) > 128:
            raise InterviewWaitInputError("작업 식별자가 올바르지 않습니다.")
        return normalized

    @staticmethod
    def _active_key(requester_user_id: str, employee_id: str) -> str:
        return f"{requester_user_id}:{employee_id}"

    @staticmethod
    def _append_operation(values: tuple[str, ...], operation_id: str) -> tuple[str, ...]:
        return values if operation_id in values else (*values, operation_id)

    @staticmethod
    def _history(
        *,
        wait: InterviewWait,
        from_status: InterviewWaitStatus | None,
        to_status: InterviewWaitStatus,
        reason: str,
        actor_user_id: str | None,
        operation_id: str,
        occurred_at: datetime,
    ) -> InterviewWaitHistory:
        return InterviewWaitHistory(
            id=str(uuid5(NAMESPACE_URL, f"interview-wait-history:{operation_id}")),
            wait_id=wait.id,
            from_status=from_status,
            to_status=to_status,
            reason=reason,
            actor_user_id=actor_user_id,
            operation_id=operation_id,
            occurred_at=occurred_at,
        )

    @staticmethod
    def _previous_status(wait: InterviewWait) -> InterviewWaitStatus:
        if wait.status == InterviewWaitStatus.READY:
            return InterviewWaitStatus.WAITING
        if wait.status == InterviewWaitStatus.COMPLETED:
            return InterviewWaitStatus.READY
        if wait.status in {InterviewWaitStatus.CANCELLED, InterviewWaitStatus.EXPIRED}:
            return (
                InterviewWaitStatus.READY
                if wait.ready_at is not None
                else InterviewWaitStatus.WAITING
            )
        raise InterviewWaitOperationConflictError()

    @staticmethod
    def _transition_time(wait: InterviewWait) -> datetime:
        if wait.status == InterviewWaitStatus.READY and wait.ready_at is not None:
            return wait.ready_at
        if wait.status == InterviewWaitStatus.COMPLETED and wait.completed_at is not None:
            return wait.completed_at
        if wait.status == InterviewWaitStatus.CANCELLED and wait.cancelled_at is not None:
            return wait.cancelled_at
        if wait.status == InterviewWaitStatus.EXPIRED:
            return wait.expires_at
        return wait.requested_at

    @staticmethod
    def _transition_reason(status: InterviewWaitStatus) -> str:
        return {
            InterviewWaitStatus.READY: "대상 직원이 재석 상태로 변경됨",
            InterviewWaitStatus.COMPLETED: "면담 완료 처리",
            InterviewWaitStatus.CANCELLED: "면담 대기 취소",
            InterviewWaitStatus.EXPIRED: "면담 대기 만료",
        }.get(status, "면담 대기 상태 변경")

    @staticmethod
    def _is_allowed_transition(
        from_status: InterviewWaitStatus,
        to_status: InterviewWaitStatus,
    ) -> bool:
        return (
            (from_status == InterviewWaitStatus.WAITING and to_status == InterviewWaitStatus.READY)
            or (
                from_status in ACTIVE_WAIT_STATUSES
                and to_status in {InterviewWaitStatus.CANCELLED, InterviewWaitStatus.EXPIRED}
            )
            or (
                from_status == InterviewWaitStatus.READY
                and to_status == InterviewWaitStatus.COMPLETED
            )
        )


class EmployeeInterviewCoordinator:
    """직원 상태 쓰기와 면담 후속 단계를 순서대로 보완하는 조정자."""

    def __init__(
        self,
        employee_service: EmployeeService,
        interview_wait_service: InterviewWaitService,
    ) -> None:
        self._employees = employee_service
        self._waits = interview_wait_service

    def record_mock_observation(
        self,
        actor: User,
        command: RecordEmployeeObservationCommand,
    ) -> EmployeeObservation:
        result = self._employees.record_mock_observation_result(actor, command)
        self._handle_return_transition(
            result.transition,
            source_operation_id=command.event_id,
            actor_user_id=actor.id,
        )
        return result.observation

    def clear_status_override(
        self,
        actor: User,
        command: ClearStatusOverrideCommand,
        *,
        ip_fingerprint: str | None,
    ) -> Employee:
        result = self._employees.clear_status_override_result(
            actor,
            command,
            ip_fingerprint=ip_fingerprint,
        )
        self._handle_return_transition(
            result.transition,
            source_operation_id=command.operation_id,
            actor_user_id=actor.id,
        )
        return result.employee

    def set_status_override(
        self,
        actor: User,
        command: SetStatusOverrideCommand,
        *,
        ip_fingerprint: str | None,
    ) -> Employee:
        result = self._employees.set_status_override_result(
            actor,
            command,
            ip_fingerprint=ip_fingerprint,
        )
        self._handle_return_transition(
            result.transition,
            source_operation_id=command.operation_id,
            actor_user_id=actor.id,
        )
        return result.employee

    def deactivate_employee(
        self,
        actor: User,
        employee_id: str,
        *,
        expected_version: int,
        operation_id: str,
        ip_fingerprint: str | None,
    ) -> Employee:
        self._waits.cancel_for_employee(
            employee_id,
            source_operation_id=operation_id,
            actor_user_id=actor.id,
        )
        return self._employees.deactivate_employee(
            actor,
            employee_id,
            expected_version=expected_version,
            operation_id=operation_id,
            ip_fingerprint=ip_fingerprint,
        )

    def _handle_return_transition(
        self,
        transition: EmployeeStatusTransition,
        *,
        source_operation_id: str,
        actor_user_id: str,
    ) -> None:
        if (
            transition.status_changed
            and transition.from_status in _ABSENT_STATUSES
            and transition.to_status in _PRESENT_STATUSES
        ):
            self._waits.mark_employee_ready(
                transition.employee_id,
                source_operation_id=source_operation_id,
                actor_user_id=actor_user_id,
            )
