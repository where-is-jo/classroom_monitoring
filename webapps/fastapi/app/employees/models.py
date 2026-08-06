"""직원 프로필, 상태, 관측과 command 도메인 값."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class EmployeeStatus(StrEnum):
    WORKING = "WORKING"
    ON_CALL = "ON_CALL"
    AWAY = "AWAY"
    OFFSITE = "OFFSITE"


class StatusSource(StrEnum):
    MANUAL = "MANUAL"
    MOCK = "MOCK"
    TIME_POLICY = "TIME_POLICY"
    SYSTEM = "SYSTEM"


MANUAL_OVERRIDE_STATUSES = frozenset(
    {EmployeeStatus.AWAY, EmployeeStatus.OFFSITE}
)


@dataclass(frozen=True)
class EmployeeCurrentStatus:
    status: EmployeeStatus
    source: StatusSource
    reason: str
    effective_at: datetime
    last_person_seen_at: datetime | None


@dataclass(frozen=True)
class EmployeeOverride:
    status: EmployeeStatus
    reason: str
    actor_user_id: str
    starts_at: datetime
    ends_at: datetime | None


@dataclass(frozen=True)
class Employee:
    id: str
    employee_no: str
    user_id: str | None
    display_name: str
    department: str
    position: str
    office_zone: str
    is_active: bool
    current_status: EmployeeCurrentStatus
    active_override: EmployeeOverride | None
    created_at: datetime
    updated_at: datetime
    version: int
    created_operation_id: str
    last_operation_id: str
    operation_ids: tuple[str, ...]


@dataclass(frozen=True)
class EmployeePage:
    items: list[Employee]
    total: int


@dataclass(frozen=True)
class EmployeeStatusHistory:
    id: str
    employee_id: str
    from_status: EmployeeStatus | None
    to_status: EmployeeStatus
    source: StatusSource
    reason: str
    actor_user_id: str | None
    operation_id: str
    occurred_at: datetime


@dataclass(frozen=True)
class EmployeeStatusHistoryPage:
    items: list[EmployeeStatusHistory]
    total: int


@dataclass(frozen=True)
class EmployeeObservation:
    event_id: str
    employee_id: str
    person_present: bool
    phone_detected: bool
    confidence: float
    observed_at: datetime
    received_at: datetime
    resulting_status: EmployeeStatus
    status_changed: bool


@dataclass(frozen=True)
class EmployeeStatusTransition:
    employee_id: str
    from_status: EmployeeStatus
    to_status: EmployeeStatus
    status_changed: bool


@dataclass(frozen=True)
class EmployeeObservationResult:
    observation: EmployeeObservation
    transition: EmployeeStatusTransition


@dataclass(frozen=True)
class EmployeeMutationResult:
    employee: Employee
    transition: EmployeeStatusTransition


@dataclass(frozen=True)
class CreateEmployeeCommand:
    employee_no: str
    user_id: str | None
    display_name: str
    department: str
    position: str
    office_zone: str
    operation_id: str


@dataclass(frozen=True)
class UpdateEmployeeCommand:
    employee_id: str
    expected_version: int
    operation_id: str
    employee_no: str | None = None
    change_user_link: bool = False
    user_id: str | None = None
    display_name: str | None = None
    department: str | None = None
    position: str | None = None
    office_zone: str | None = None
    is_active: bool | None = None


@dataclass(frozen=True)
class SetStatusOverrideCommand:
    employee_id: str
    status: EmployeeStatus
    reason: str
    ends_at: datetime | None
    expected_version: int
    operation_id: str


@dataclass(frozen=True)
class ClearStatusOverrideCommand:
    employee_id: str
    expected_version: int
    operation_id: str


@dataclass(frozen=True)
class RecordEmployeeObservationCommand:
    event_id: str
    employee_id: str
    person_present: bool
    phone_detected: bool
    confidence: float
    observed_at: datetime


@dataclass(frozen=True)
class EvaluateEmployeeStatusesCommand:
    operation_id: str


@dataclass(frozen=True)
class EmployeeStatusEvaluation:
    evaluated_at: datetime
    evaluated_count: int
    changed_count: int
