"""면담 대기 저장소 외부 I/O 포트."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .models import (
    InterviewWait,
    InterviewWaitHistory,
    InterviewWaitPage,
    InterviewWaitStatus,
)


class InterviewWaitRepository(Protocol):
    def create_wait(
        self, wait: InterviewWait, history: InterviewWaitHistory
    ) -> InterviewWait: ...

    def get_wait(self, wait_id: str) -> InterviewWait | None: ...

    def get_wait_by_operation_id(self, operation_id: str) -> InterviewWait | None: ...

    def get_active_wait(
        self, requester_user_id: str, employee_id: str
    ) -> InterviewWait | None: ...

    def list_waits(
        self,
        *,
        requester_user_id: str | None,
        employee_id: str | None,
        status: InterviewWaitStatus | None,
        limit: int,
        offset: int,
    ) -> InterviewWaitPage: ...

    def list_active_for_employee(self, employee_id: str) -> list[InterviewWait]: ...

    def list_expired_candidates(self, now: datetime) -> list[InterviewWait]: ...

    def replace_wait(
        self,
        wait: InterviewWait,
        *,
        expected_version: int,
        history: InterviewWaitHistory,
    ) -> InterviewWait | None: ...

    def append_history(self, history: InterviewWaitHistory) -> InterviewWaitHistory: ...

    def get_history_by_operation_id(
        self, operation_id: str
    ) -> InterviewWaitHistory | None: ...

    def list_history(self, wait_id: str) -> list[InterviewWaitHistory]: ...
