"""직원 기능의 HTTP 요청·응답 Pydantic 스키마."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from .models import (
    Employee,
    EmployeeObservation,
    EmployeeOverride,
    EmployeePage,
    EmployeeStatus,
    EmployeeStatusEvaluation,
    EmployeeStatusHistory,
    EmployeeStatusHistoryPage,
    StatusSource,
)


def _require_aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        raise ValueError("UTC offset이 포함된 시각이 필요합니다.")
    return None if value is None else value.astimezone(UTC)


class EmployeeCurrentStatusResponse(BaseModel):
    status: EmployeeStatus
    source: StatusSource
    reason: str
    effective_at: datetime
    last_person_seen_at: datetime | None


class EmployeeOverrideResponse(BaseModel):
    status: EmployeeStatus
    reason: str
    actor_user_id: str
    starts_at: datetime
    ends_at: datetime | None

    @classmethod
    def from_override(cls, override: EmployeeOverride) -> EmployeeOverrideResponse:
        return cls(
            status=override.status,
            reason=override.reason,
            actor_user_id=override.actor_user_id,
            starts_at=override.starts_at,
            ends_at=override.ends_at,
        )


class EmployeeResponse(BaseModel):
    id: str
    employee_no: str
    user_id: str | None
    display_name: str
    department: str
    position: str
    office_zone: str
    is_active: bool
    current_status: EmployeeCurrentStatusResponse
    active_override: EmployeeOverrideResponse | None
    created_at: datetime
    updated_at: datetime
    version: int

    @classmethod
    def from_employee(cls, employee: Employee) -> EmployeeResponse:
        return cls(
            id=employee.id,
            employee_no=employee.employee_no,
            user_id=employee.user_id,
            display_name=employee.display_name,
            department=employee.department,
            position=employee.position,
            office_zone=employee.office_zone,
            is_active=employee.is_active,
            current_status=EmployeeCurrentStatusResponse(
                status=employee.current_status.status,
                source=employee.current_status.source,
                reason=employee.current_status.reason,
                effective_at=employee.current_status.effective_at,
                last_person_seen_at=employee.current_status.last_person_seen_at,
            ),
            active_override=(
                None
                if employee.active_override is None
                else EmployeeOverrideResponse.from_override(employee.active_override)
            ),
            created_at=employee.created_at,
            updated_at=employee.updated_at,
            version=employee.version,
        )


class EmployeeListResponse(BaseModel):
    items: list[EmployeeResponse]
    total: int
    limit: int
    offset: int

    @classmethod
    def from_page(
        cls,
        page: EmployeePage,
        *,
        limit: int,
        offset: int,
    ) -> EmployeeListResponse:
        return cls(
            items=[EmployeeResponse.from_employee(item) for item in page.items],
            total=page.total,
            limit=limit,
            offset=offset,
        )


class EmployeeStatusHistoryResponse(BaseModel):
    id: str
    employee_id: str
    from_status: EmployeeStatus | None
    to_status: EmployeeStatus
    source: StatusSource
    reason: str
    actor_user_id: str | None
    operation_id: str
    occurred_at: datetime

    @classmethod
    def from_history(
        cls,
        history: EmployeeStatusHistory,
    ) -> EmployeeStatusHistoryResponse:
        return cls(**history.__dict__)


class EmployeeStatusHistoryListResponse(BaseModel):
    items: list[EmployeeStatusHistoryResponse]
    total: int
    limit: int
    offset: int

    @classmethod
    def from_page(
        cls,
        page: EmployeeStatusHistoryPage,
        *,
        limit: int,
        offset: int,
    ) -> EmployeeStatusHistoryListResponse:
        return cls(
            items=[EmployeeStatusHistoryResponse.from_history(item) for item in page.items],
            total=page.total,
            limit=limit,
            offset=offset,
        )


class CreateEmployeeRequest(BaseModel):
    employee_no: str = Field(min_length=1, max_length=50)
    user_id: str | None = Field(default=None, max_length=100)
    display_name: str = Field(min_length=1, max_length=100)
    department: str = Field(min_length=1, max_length=100)
    position: str = Field(min_length=1, max_length=100)
    office_zone: str = Field(min_length=1, max_length=100)
    operation_id: UUID = Field(default_factory=uuid4)


class UpdateEmployeeRequest(BaseModel):
    expected_version: int = Field(ge=0)
    operation_id: UUID = Field(default_factory=uuid4)
    employee_no: str | None = Field(default=None, min_length=1, max_length=50)
    user_id: str | None = Field(default=None, max_length=100)
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    department: str | None = Field(default=None, min_length=1, max_length=100)
    position: str | None = Field(default=None, min_length=1, max_length=100)
    office_zone: str | None = Field(default=None, min_length=1, max_length=100)
    is_active: bool | None = None

    @model_validator(mode="after")
    def _at_least_one_change(self) -> UpdateEmployeeRequest:
        changed_fields = self.model_fields_set - {
            "expected_version",
            "operation_id",
            "csrf_token",
        }
        if not changed_fields:
            raise ValueError("변경할 필드가 하나 이상 필요합니다.")
        return self


class DeactivateEmployeeRequest(BaseModel):
    expected_version: int = Field(ge=0)
    operation_id: UUID = Field(default_factory=uuid4)


class SetStatusOverrideRequest(BaseModel):
    status: EmployeeStatus
    reason: str = Field(min_length=1, max_length=500)
    ends_at: datetime | None = None
    expected_version: int = Field(ge=0)
    operation_id: UUID = Field(default_factory=uuid4)

    @field_validator("ends_at", mode="before")
    @classmethod
    def _empty_ends_at_is_none(cls, value: object) -> object:
        return None if value == "" else value

    _aware_ends_at = field_validator("ends_at")(_require_aware)


class ClearStatusOverrideRequest(BaseModel):
    expected_version: int = Field(ge=0)
    operation_id: UUID = Field(default_factory=uuid4)


class EvaluateEmployeeStatusesRequest(BaseModel):
    operation_id: UUID = Field(default_factory=uuid4)


class EmployeeStatusEvaluationResponse(BaseModel):
    evaluated_at: datetime
    evaluated_count: int
    changed_count: int

    @classmethod
    def from_evaluation(
        cls,
        evaluation: EmployeeStatusEvaluation,
    ) -> EmployeeStatusEvaluationResponse:
        return cls(**evaluation.__dict__)


class MockEmployeeObservationRequest(BaseModel):
    event_id: UUID
    employee_id: str = Field(min_length=1, max_length=100)
    person_present: bool
    phone_detected: bool
    confidence: float = Field(ge=0, le=1)
    observed_at: datetime

    _aware_observed_at = field_validator("observed_at")(_require_aware)


class EmployeeObservationResponse(BaseModel):
    event_id: str
    employee_id: str
    person_present: bool
    phone_detected: bool
    confidence: float
    observed_at: datetime
    received_at: datetime
    resulting_status: EmployeeStatus
    status_changed: bool

    @classmethod
    def from_observation(
        cls,
        observation: EmployeeObservation,
    ) -> EmployeeObservationResponse:
        return cls(**observation.__dict__)


class CreateEmployeeForm(CreateEmployeeRequest):
    csrf_token: str = Field(min_length=1)


class UpdateEmployeeForm(UpdateEmployeeRequest):
    csrf_token: str = Field(min_length=1)


class DeactivateEmployeeForm(DeactivateEmployeeRequest):
    csrf_token: str = Field(min_length=1)


class SetStatusOverrideForm(SetStatusOverrideRequest):
    csrf_token: str = Field(min_length=1)


class ClearStatusOverrideForm(ClearStatusOverrideRequest):
    csrf_token: str = Field(min_length=1)


class EvaluateEmployeeStatusesForm(EvaluateEmployeeStatusesRequest):
    csrf_token: str = Field(min_length=1)


class MockEmployeeObservationForm(MockEmployeeObservationRequest):
    csrf_token: str = Field(min_length=1)
