"""외부 의존 없는 면담 대기 저장소."""

from __future__ import annotations

from datetime import datetime
from threading import RLock

from ..errors import InterviewWaitDuplicateError, InterviewWaitOperationConflictError
from ..models import (
    ACTIVE_WAIT_STATUSES,
    InterviewWait,
    InterviewWaitHistory,
    InterviewWaitPage,
    InterviewWaitStatus,
)


class InMemoryInterviewWaitRepository:
    def __init__(self) -> None:
        self._waits: dict[str, InterviewWait] = {}
        self._history: dict[str, InterviewWaitHistory] = {}
        self._lock = RLock()

    def create_wait(self, wait: InterviewWait, history: InterviewWaitHistory) -> InterviewWait:
        with self._lock:
            operation_owner = self.get_wait_by_operation_id(wait.created_operation_id)
            if operation_owner is not None:
                return operation_owner
            if wait.active_key is not None:
                active_owner = next(
                    (item for item in self._waits.values() if item.active_key == wait.active_key),
                    None,
                )
                if active_owner is not None:
                    raise InterviewWaitDuplicateError()
            self._waits[wait.id] = wait
            self.append_history(history)
            return wait

    def get_wait(self, wait_id: str) -> InterviewWait | None:
        with self._lock:
            return self._waits.get(wait_id)

    def dashboard_snapshot(
        self,
    ) -> tuple[list[InterviewWait], list[InterviewWaitHistory]]:
        """Return an immutable-value snapshot for the local admin read model."""
        with self._lock:
            return list(self._waits.values()), list(self._history.values())

    def get_wait_by_operation_id(self, operation_id: str) -> InterviewWait | None:
        with self._lock:
            return next(
                (wait for wait in self._waits.values() if operation_id in wait.operation_ids),
                None,
            )

    def get_active_wait(self, requester_user_id: str, employee_id: str) -> InterviewWait | None:
        active_key = f"{requester_user_id}:{employee_id}"
        with self._lock:
            return next(
                (wait for wait in self._waits.values() if wait.active_key == active_key),
                None,
            )

    def list_waits(
        self,
        *,
        requester_user_id: str | None,
        employee_id: str | None,
        status: InterviewWaitStatus | None,
        limit: int,
        offset: int,
    ) -> InterviewWaitPage:
        with self._lock:
            waits = list(self._waits.values())
        if requester_user_id is not None:
            waits = [wait for wait in waits if wait.requester_user_id == requester_user_id]
        if employee_id is not None:
            waits = [wait for wait in waits if wait.employee_id == employee_id]
        if status is not None:
            waits = [wait for wait in waits if wait.status == status]
        waits.sort(key=lambda wait: (wait.requested_at, wait.id), reverse=True)
        return InterviewWaitPage(items=waits[offset : offset + limit], total=len(waits))

    def list_active_for_employee(self, employee_id: str) -> list[InterviewWait]:
        with self._lock:
            waits = [
                wait
                for wait in self._waits.values()
                if wait.employee_id == employee_id and wait.status in ACTIVE_WAIT_STATUSES
            ]
        return sorted(waits, key=lambda wait: (wait.requested_at, wait.id))

    def list_expired_candidates(self, now: datetime) -> list[InterviewWait]:
        with self._lock:
            waits = [
                wait
                for wait in self._waits.values()
                if wait.status in ACTIVE_WAIT_STATUSES and wait.expires_at <= now
            ]
        return sorted(waits, key=lambda wait: (wait.expires_at, wait.id))

    def replace_wait(
        self,
        wait: InterviewWait,
        *,
        expected_version: int,
        history: InterviewWaitHistory,
    ) -> InterviewWait | None:
        with self._lock:
            current = self._waits.get(wait.id)
            if current is None or current.version != expected_version:
                operation_owner = self.get_wait_by_operation_id(history.operation_id)
                return (
                    operation_owner if operation_owner and operation_owner.id == wait.id else None
                )
            if wait.active_key is not None:
                active_owner = next(
                    (
                        item
                        for item in self._waits.values()
                        if item.active_key == wait.active_key and item.id != wait.id
                    ),
                    None,
                )
                if active_owner is not None:
                    raise InterviewWaitDuplicateError()
            self._waits[wait.id] = wait
            self.append_history(history)
            return wait

    def append_history(self, history: InterviewWaitHistory) -> InterviewWaitHistory:
        with self._lock:
            existing = self.get_history_by_operation_id(history.operation_id)
            if existing is not None:
                if existing.wait_id != history.wait_id or existing.to_status != history.to_status:
                    raise InterviewWaitOperationConflictError()
                return existing
            self._history[history.id] = history
            return history

    def get_history_by_operation_id(self, operation_id: str) -> InterviewWaitHistory | None:
        with self._lock:
            return next(
                (item for item in self._history.values() if item.operation_id == operation_id),
                None,
            )

    def list_history(self, wait_id: str) -> list[InterviewWaitHistory]:
        with self._lock:
            history = [item for item in self._history.values() if item.wait_id == wait_id]
        return sorted(history, key=lambda item: (item.occurred_at, item.id))
