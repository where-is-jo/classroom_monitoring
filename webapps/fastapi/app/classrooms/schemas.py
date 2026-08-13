"""강의실 좌석 조회 HTTP schema."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from .models import (
    Classroom,
    ClassroomOccupancySummary,
    ClassroomPage,
    Seat,
    SeatAssignmentInfo,
    SeatGeometry,
    SeatPage,
)


class ClassroomCreateRequest(BaseModel):
    """강의실 생성 요청."""

    code: str
    name: str
    location: str


class ClassroomUpdateRequest(BaseModel):
    """강의실 수정 요청. 전달한 필드만 갱신한다."""

    code: str | None = None
    name: str | None = None
    location: str | None = None
    is_active: bool | None = None


class ClassroomResponse(BaseModel):
    id: str
    code: str
    name: str
    location: str
    is_active: bool
    created_at: datetime

    @classmethod
    def from_domain(cls, item: Classroom) -> ClassroomResponse:
        return cls(
            id=item.id,
            code=item.code,
            name=item.name,
            location=item.location,
            is_active=item.is_active,
            created_at=item.created_at,
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


class GeometryResponse(BaseModel):
    x: float
    y: float
    width: float
    height: float

    @classmethod
    def from_domain(cls, item: SeatGeometry) -> GeometryResponse:
        return cls(x=item.x, y=item.y, width=item.width, height=item.height)


class GeometryRequest(BaseModel):
    """좌석 geometry 입력. 0~1 정규화 좌표다."""

    x: float
    y: float
    width: float
    height: float

    def to_domain(self) -> SeatGeometry:
        return SeatGeometry(x=self.x, y=self.y, width=self.width, height=self.height)


class SeatCreateRequest(BaseModel):
    """좌석 생성 요청."""

    code: str
    label: str
    row: int | None = None
    column: int | None = None
    geometry: GeometryRequest | None = None


class SeatUpdateRequest(BaseModel):
    """좌석 수정 요청. 전달한 필드만 갱신한다."""

    code: str | None = None
    label: str | None = None
    row: int | None = None
    column: int | None = None
    geometry: GeometryRequest | None = None
    is_active: bool | None = None


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
    row: int | None = None
    column: int | None = None
    geometry: GeometryResponse | None = None
    is_active: bool
    current_occupancy: CurrentOccupancyResponse
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, item: Seat) -> SeatResponse:
        return cls(
            id=item.id,
            classroom_id=item.classroom_id,
            code=item.code,
            label=item.label,
            row=item.row,
            column=item.column,
            geometry=(
                None if item.geometry is None else GeometryResponse.from_domain(item.geometry)
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


class OccupancySummaryResponse(BaseModel):
    classroom: ClassroomResponse
    seats: list[SeatResponse]
    total: int
    occupied_count: int
    vacant_count: int
    unknown_count: int
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
            last_observed_at=value.last_observed_at,
        )


# ============================================================
# 좌석-학생 지정 스키마
# ============================================================


class SeatAssignmentRequest(BaseModel):
    """좌석-학생 지정 요청."""

    student_id: str


class SeatAssignmentResponse(BaseModel):
    """좌석-학생 지정 응답."""

    seat_id: str
    student_id: str
    student_name: str
    assigned_at: datetime

    @classmethod
    def from_domain(cls, info: SeatAssignmentInfo) -> SeatAssignmentResponse:
        return cls(
            seat_id=info.seat_id,
            student_id=info.student_id,
            student_name=info.student_name,
            assigned_at=info.assigned_at,
        )


class SeatAssignmentListResponse(BaseModel):
    """좌석-학생 지정 목록 응답."""

    items: list[SeatAssignmentResponse]
