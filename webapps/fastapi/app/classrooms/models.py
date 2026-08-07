"""Classroom, schedule, seat occupancy, and alert domain values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from enum import StrEnum


class SeatOccupancy(StrEnum):
    VACANT = "VACANT"
    OCCUPIED = "OCCUPIED"
    UNKNOWN = "UNKNOWN"


class OccupancySource(StrEnum):
    SYSTEM = "SYSTEM"
    MOCK = "MOCK"


class AfterHoursAlertStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


class ObservationBatchStatus(StrEnum):
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"


@dataclass(frozen=True)
class ClassroomSchedule:
    day_of_week: int
    opens_at: time
    closes_at: time


@dataclass(frozen=True)
class Classroom:
    id: str
    code: str
    name: str
    location: str
    timezone: str
    schedules: tuple[ClassroomSchedule, ...]
    after_hours_grace_minutes: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    version: int
    created_operation_id: str
    last_operation_id: str
    operation_ids: tuple[str, ...]
    responsible_staff_user_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClassroomPage:
    items: list[Classroom]
    total: int


@dataclass(frozen=True)
class SeatGeometry:
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class SeatCurrentOccupancy:
    state: SeatOccupancy
    source: OccupancySource
    confidence: float | None
    observed_at: datetime | None
    event_id: str | None


@dataclass(frozen=True)
class Seat:
    id: str
    classroom_id: str
    code: str
    label: str
    geometry: SeatGeometry | None
    is_active: bool
    current_occupancy: SeatCurrentOccupancy
    created_at: datetime
    updated_at: datetime
    version: int
    created_operation_id: str
    last_operation_id: str
    operation_ids: tuple[str, ...]


@dataclass(frozen=True)
class SeatPage:
    items: list[Seat]
    total: int


@dataclass(frozen=True)
class SeatOccupancyHistory:
    id: str
    seat_id: str
    classroom_id: str
    event_id: str
    from_state: SeatOccupancy
    to_state: SeatOccupancy
    occupied: bool
    confidence: float
    observed_at: datetime
    received_at: datetime
    applied_to_current: bool
    state_changed: bool


@dataclass(frozen=True)
class SeatOccupancyHistoryPage:
    items: list[SeatOccupancyHistory]
    total: int


@dataclass(frozen=True)
class AfterHoursAlert:
    id: str
    dedupe_key: str
    classroom_id: str
    seat_id: str
    business_date: date
    status: AfterHoursAlertStatus
    detected_at: datetime
    resolved_at: datetime | None
    resolved_by_user_id: str | None
    created_operation_id: str
    last_operation_id: str
    operation_ids: tuple[str, ...]
    version: int


@dataclass(frozen=True)
class AfterHoursAlertPage:
    items: list[AfterHoursAlert]
    total: int


@dataclass(frozen=True)
class SeatObservation:
    seat_id: str
    occupied: bool
    confidence: float


@dataclass(frozen=True)
class SeatObservationBatchRecord:
    event_id: str
    classroom_id: str
    actor_user_id: str
    observed_at: datetime
    observations: tuple[SeatObservation, ...]
    status: ObservationBatchStatus
    processed_count: int
    changed_count: int
    alert_count: int
    received_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True)
class SeatObservationBatchResult:
    event_id: str
    processed_count: int
    changed_count: int
    alert_count: int


@dataclass(frozen=True)
class ClassroomOccupancySummary:
    classroom: Classroom
    seats: list[Seat]
    total: int
    occupied_count: int
    vacant_count: int
    unknown_count: int
    is_operating: bool
    last_observed_at: datetime | None


@dataclass(frozen=True)
class CreateClassroomCommand:
    code: str
    name: str
    location: str
    timezone: str
    after_hours_grace_minutes: int
    operation_id: str
    responsible_staff_user_ids: tuple[str, ...] = ()
    entity_id: str | None = None


@dataclass(frozen=True)
class UpdateClassroomCommand:
    classroom_id: str
    code: str
    name: str
    location: str
    timezone: str
    after_hours_grace_minutes: int
    expected_version: int
    operation_id: str
    responsible_staff_user_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReplaceSchedulesCommand:
    classroom_id: str
    schedules: tuple[ClassroomSchedule, ...]
    expected_version: int
    operation_id: str


@dataclass(frozen=True)
class CreateSeatCommand:
    classroom_id: str
    code: str
    label: str
    geometry: SeatGeometry | None
    operation_id: str
    entity_id: str | None = None


@dataclass(frozen=True)
class UpdateSeatCommand:
    seat_id: str
    code: str
    label: str
    geometry: SeatGeometry | None
    expected_version: int
    operation_id: str


@dataclass(frozen=True)
class RecordSeatObservationBatchCommand:
    event_id: str
    classroom_id: str
    observed_at: datetime
    observations: tuple[SeatObservation, ...]


@dataclass(frozen=True)
class ResolveAfterHoursAlertCommand:
    alert_id: str
    expected_version: int
    operation_id: str
