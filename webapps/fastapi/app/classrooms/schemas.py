"""Pydantic HTTP schemas for classroom resources."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import (
    AfterHoursAlert,
    AfterHoursAlertPage,
    Classroom,
    ClassroomOccupancySummary,
    ClassroomPage,
    ClassroomSchedule,
    Seat,
    SeatGeometry,
    SeatObservationBatchResult,
    SeatOccupancyHistory,
    SeatOccupancyHistoryPage,
    SeatPage,
)


class ScheduleSchema(BaseModel):
    day_of_week: int = Field(ge=0, le=6)
    opens_at: time
    closes_at: time

    @model_validator(mode="after")
    def validate_same_day(self) -> ScheduleSchema:
        if self.closes_at <= self.opens_at:
            raise ValueError("closes_at must be later than opens_at")
        return self

    def to_domain(self) -> ClassroomSchedule:
        return ClassroomSchedule(
            day_of_week=self.day_of_week,
            opens_at=self.opens_at,
            closes_at=self.closes_at,
        )


class ClassroomResponse(BaseModel):
    id: str
    code: str
    name: str
    location: str
    timezone: str
    schedules: list[ScheduleSchema]
    after_hours_grace_minutes: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    version: int

    @classmethod
    def from_domain(cls, item: Classroom) -> ClassroomResponse:
        return cls(
            id=item.id,
            code=item.code,
            name=item.name,
            location=item.location,
            timezone=item.timezone,
            schedules=[
                ScheduleSchema(
                    day_of_week=value.day_of_week,
                    opens_at=value.opens_at,
                    closes_at=value.closes_at,
                )
                for value in item.schedules
            ],
            after_hours_grace_minutes=item.after_hours_grace_minutes,
            is_active=item.is_active,
            created_at=item.created_at,
            updated_at=item.updated_at,
            version=item.version,
        )


class ClassroomListResponse(BaseModel):
    items: list[ClassroomResponse]
    total: int
    limit: int
    offset: int

    @classmethod
    def from_page(cls, page: ClassroomPage, limit: int, offset: int) -> ClassroomListResponse:
        return cls(
            items=[ClassroomResponse.from_domain(item) for item in page.items],
            total=page.total,
            limit=limit,
            offset=offset,
        )


class CreateClassroomRequest(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    location: str = Field(min_length=1, max_length=200)
    timezone: str = Field(min_length=1, max_length=100)
    after_hours_grace_minutes: int = Field(default=10, ge=0, le=1440)
    operation_id: UUID


class UpdateClassroomRequest(CreateClassroomRequest):
    expected_version: int = Field(ge=0)


class ReplaceSchedulesRequest(BaseModel):
    schedules: list[ScheduleSchema] = Field(max_length=7)
    expected_version: int = Field(ge=0)
    operation_id: UUID


class MutationRequest(BaseModel):
    expected_version: int = Field(ge=0)
    operation_id: UUID


class GeometrySchema(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def validate_bounds(self) -> GeometrySchema:
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("geometry must fit inside normalized bounds")
        return self

    def to_domain(self) -> SeatGeometry:
        return SeatGeometry(x=self.x, y=self.y, width=self.width, height=self.height)


class CurrentOccupancyResponse(BaseModel):
    state: str
    source: str
    confidence: float | None
    observed_at: datetime | None
    event_id: str | None


class SeatResponse(BaseModel):
    id: str
    classroom_id: str
    code: str
    label: str
    geometry: GeometrySchema | None
    is_active: bool
    current_occupancy: CurrentOccupancyResponse
    created_at: datetime
    updated_at: datetime
    version: int

    @classmethod
    def from_domain(cls, item: Seat) -> SeatResponse:
        return cls(
            id=item.id,
            classroom_id=item.classroom_id,
            code=item.code,
            label=item.label,
            geometry=(
                None
                if item.geometry is None
                else GeometrySchema(
                    x=item.geometry.x,
                    y=item.geometry.y,
                    width=item.geometry.width,
                    height=item.geometry.height,
                )
            ),
            is_active=item.is_active,
            current_occupancy=CurrentOccupancyResponse(
                state=item.current_occupancy.state.value,
                source=item.current_occupancy.source.value,
                confidence=item.current_occupancy.confidence,
                observed_at=item.current_occupancy.observed_at,
                event_id=item.current_occupancy.event_id,
            ),
            created_at=item.created_at,
            updated_at=item.updated_at,
            version=item.version,
        )


class SeatListResponse(BaseModel):
    items: list[SeatResponse]
    total: int
    limit: int
    offset: int

    @classmethod
    def from_page(cls, page: SeatPage, limit: int, offset: int) -> SeatListResponse:
        return cls(
            items=[SeatResponse.from_domain(item) for item in page.items],
            total=page.total,
            limit=limit,
            offset=offset,
        )


class CreateSeatRequest(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=200)
    geometry: GeometrySchema | None = None
    operation_id: UUID


class UpdateSeatRequest(CreateSeatRequest):
    expected_version: int = Field(ge=0)


class OccupancySummaryResponse(BaseModel):
    classroom: ClassroomResponse
    seats: list[SeatResponse]
    total: int
    occupied_count: int
    vacant_count: int
    unknown_count: int
    is_operating: bool
    last_observed_at: datetime | None

    @classmethod
    def from_domain(cls, value: ClassroomOccupancySummary) -> OccupancySummaryResponse:
        return cls(
            classroom=ClassroomResponse.from_domain(value.classroom),
            seats=[SeatResponse.from_domain(item) for item in value.seats],
            total=value.total,
            occupied_count=value.occupied_count,
            vacant_count=value.vacant_count,
            unknown_count=value.unknown_count,
            is_operating=value.is_operating,
            last_observed_at=value.last_observed_at,
        )


class OccupancyHistoryResponse(BaseModel):
    id: str
    seat_id: str
    classroom_id: str
    event_id: str
    from_state: str
    to_state: str
    occupied: bool
    confidence: float
    observed_at: datetime
    received_at: datetime
    applied_to_current: bool
    state_changed: bool

    @classmethod
    def from_domain(cls, item: SeatOccupancyHistory) -> OccupancyHistoryResponse:
        return cls(
            id=item.id,
            seat_id=item.seat_id,
            classroom_id=item.classroom_id,
            event_id=item.event_id,
            from_state=item.from_state.value,
            to_state=item.to_state.value,
            occupied=item.occupied,
            confidence=item.confidence,
            observed_at=item.observed_at,
            received_at=item.received_at,
            applied_to_current=item.applied_to_current,
            state_changed=item.state_changed,
        )


class OccupancyHistoryListResponse(BaseModel):
    items: list[OccupancyHistoryResponse]
    total: int
    limit: int
    offset: int

    @classmethod
    def from_page(
        cls, page: SeatOccupancyHistoryPage, limit: int, offset: int
    ) -> OccupancyHistoryListResponse:
        return cls(
            items=[OccupancyHistoryResponse.from_domain(item) for item in page.items],
            total=page.total,
            limit=limit,
            offset=offset,
        )


class SeatObservationSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seat_id: str = Field(min_length=1, max_length=128)
    occupied: bool
    confidence: float = Field(ge=0, le=1)


class MockSeatObservationBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    classroom_id: str = Field(min_length=1, max_length=128)
    observed_at: datetime
    seats: list[SeatObservationSchema] = Field(min_length=1, max_length=200)


class SeatObservationBatchResponse(BaseModel):
    event_id: str
    processed_count: int
    changed_count: int
    alert_count: int

    @classmethod
    def from_domain(cls, item: SeatObservationBatchResult) -> SeatObservationBatchResponse:
        return cls(
            event_id=item.event_id,
            processed_count=item.processed_count,
            changed_count=item.changed_count,
            alert_count=item.alert_count,
        )


class AfterHoursAlertResponse(BaseModel):
    id: str
    classroom_id: str
    seat_id: str
    business_date: date
    status: str
    detected_at: datetime
    resolved_at: datetime | None
    resolved_by_user_id: str | None
    version: int

    @classmethod
    def from_domain(cls, item: AfterHoursAlert) -> AfterHoursAlertResponse:
        return cls(
            id=item.id,
            classroom_id=item.classroom_id,
            seat_id=item.seat_id,
            business_date=item.business_date,
            status=item.status.value,
            detected_at=item.detected_at,
            resolved_at=item.resolved_at,
            resolved_by_user_id=item.resolved_by_user_id,
            version=item.version,
        )


class AfterHoursAlertListResponse(BaseModel):
    items: list[AfterHoursAlertResponse]
    total: int
    limit: int
    offset: int

    @classmethod
    def from_page(
        cls, page: AfterHoursAlertPage, limit: int, offset: int
    ) -> AfterHoursAlertListResponse:
        return cls(
            items=[AfterHoursAlertResponse.from_domain(item) for item in page.items],
            total=page.total,
            limit=limit,
            offset=offset,
        )


class ResolveAlertRequest(BaseModel):
    status: Literal["RESOLVED"]
    expected_version: int = Field(ge=0)
    operation_id: UUID


class ClassroomCreateForm(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    location: str = Field(min_length=1, max_length=200)
    timezone: str = Field(min_length=1, max_length=100)
    after_hours_grace_minutes: int = Field(ge=0, le=1440)
    operation_id: UUID


class ClassroomUpdateForm(ClassroomCreateForm):
    expected_version: int = Field(ge=0)


class ScheduleLinesForm(BaseModel):
    schedule_lines: str = Field(max_length=1000)
    expected_version: int = Field(ge=0)
    operation_id: UUID


class SeatCreateForm(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=200)
    x: str | None = Field(default=None, max_length=32)
    y: str | None = Field(default=None, max_length=32)
    width: str | None = Field(default=None, max_length=32)
    height: str | None = Field(default=None, max_length=32)
    operation_id: UUID


class SeatUpdateForm(SeatCreateForm):
    expected_version: int = Field(ge=0)


class MutationForm(BaseModel):
    expected_version: int = Field(ge=0)
    operation_id: UUID


class SeatObservationLinesForm(BaseModel):
    event_id: UUID
    classroom_id: str = Field(min_length=1, max_length=128)
    observed_at: datetime
    seat_lines: str = Field(min_length=1, max_length=10000)


class AlertResolveForm(BaseModel):
    expected_version: int = Field(ge=0)
    operation_id: UUID
