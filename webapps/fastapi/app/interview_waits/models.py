"""면담 대기 상태, 이력과 command 도메인 값."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class InterviewWaitStatus(StrEnum):
    WAITING = "WAITING"
    READY = "READY"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


ACTIVE_WAIT_STATUSES = frozenset({InterviewWaitStatus.WAITING, InterviewWaitStatus.READY})
TERMINAL_WAIT_STATUSES = frozenset(
    {
        InterviewWaitStatus.COMPLETED,
        InterviewWaitStatus.CANCELLED,
        InterviewWaitStatus.EXPIRED,
    }
)


@dataclass(frozen=True)
class InterviewWait:
    id: str
    requester_user_id: str
    employee_id: str
    status: InterviewWaitStatus
    message: str | None
    requested_at: datetime
    ready_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    expires_at: datetime
    version: int
    active_key: str | None
    created_operation_id: str
    last_operation_id: str
    operation_ids: tuple[str, ...]
    last_actor_user_id: str | None


@dataclass(frozen=True)
class InterviewWaitPage:
    items: list[InterviewWait]
    total: int


@dataclass(frozen=True)
class InterviewWaitHistory:
    id: str
    wait_id: str
    from_status: InterviewWaitStatus | None
    to_status: InterviewWaitStatus
    reason: str
    actor_user_id: str | None
    operation_id: str
    occurred_at: datetime


@dataclass(frozen=True)
class CreateInterviewWaitCommand:
    employee_id: str
    message: str | None
    operation_id: str


@dataclass(frozen=True)
class TransitionInterviewWaitCommand:
    wait_id: str
    status: InterviewWaitStatus
    operation_id: str


@dataclass(frozen=True)
class EvaluateInterviewWaitExpirationsCommand:
    operation_id: str


@dataclass(frozen=True)
class InterviewWaitExpirationResult:
    evaluated_at: datetime
    evaluated_count: int
    expired_count: int


@dataclass(frozen=True)
class InterviewWaitDisplay:
    wait: InterviewWait
    employee_name: str
    requester_name: str
