"""면담 대기 HTTP 요청·응답과 form 스키마."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from .models import (
    InterviewWait,
    InterviewWaitExpirationResult,
    InterviewWaitStatus,
)


class InterviewWaitResponse(BaseModel):
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

    @classmethod
    def from_wait(cls, wait: InterviewWait) -> "InterviewWaitResponse":
        return cls(
            id=wait.id,
            requester_user_id=wait.requester_user_id,
            employee_id=wait.employee_id,
            status=wait.status,
            message=wait.message,
            requested_at=wait.requested_at,
            ready_at=wait.ready_at,
            completed_at=wait.completed_at,
            cancelled_at=wait.cancelled_at,
            expires_at=wait.expires_at,
            version=wait.version,
        )


class InterviewWaitListResponse(BaseModel):
    items: list[InterviewWaitResponse]
    total: int
    limit: int
    offset: int


class CreateInterviewWaitRequest(BaseModel):
    employee_id: str = Field(min_length=1, max_length=128)
    message: str | None = Field(default=None, max_length=500)
    operation_id: UUID


class UpdateInterviewWaitRequest(BaseModel):
    status: Literal["CANCELLED", "COMPLETED"]
    operation_id: UUID


class EvaluateInterviewWaitExpirationsRequest(BaseModel):
    operation_id: UUID


class InterviewWaitExpirationResponse(BaseModel):
    evaluated_at: datetime
    evaluated_count: int = Field(ge=0)
    expired_count: int = Field(ge=0)

    @classmethod
    def from_result(
        cls, result: InterviewWaitExpirationResult
    ) -> "InterviewWaitExpirationResponse":
        return cls(
            evaluated_at=result.evaluated_at,
            evaluated_count=result.evaluated_count,
            expired_count=result.expired_count,
        )


class CreateInterviewWaitForm(BaseModel):
    employee_id: str = Field(min_length=1, max_length=128)
    message: str | None = Field(default=None, max_length=500)
    operation_id: UUID


class TransitionInterviewWaitForm(BaseModel):
    status: Literal["CANCELLED", "COMPLETED"]
    operation_id: UUID
