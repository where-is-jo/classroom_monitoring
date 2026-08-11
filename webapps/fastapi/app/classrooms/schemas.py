"""강의실 좌석 조회 HTTP schema."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from .models import Classroom, ClassroomOccupancySummary, ClassroomPage, Seat, SeatGeometry


class ClassroomResponse(BaseModel):
    id: str
    code: str
    name: str
    location: str

    @classmethod
    def from_domain(cls, item: Classroom) -> ClassroomResponse:
        return cls(id=item.id, code=item.code, name=item.name, location=item.location)


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


class CurrentOccupancyResponse(BaseModel):
    state: str
    source: str
    confidence: float | None
    observed_at: datetime | None
    event_id: str | None


class SeatResponse(BaseModel):
    id: str
    code: str
    label: str
    geometry: GeometryResponse | None
    current_occupancy: CurrentOccupancyResponse

    @classmethod
    def from_domain(cls, item: Seat) -> SeatResponse:
        return cls(
            id=item.id,
            code=item.code,
            label=item.label,
            geometry=(
                None if item.geometry is None else GeometryResponse.from_domain(item.geometry)
            ),
            current_occupancy=CurrentOccupancyResponse(
                state=item.current_occupancy.state.value,
                source=item.current_occupancy.source.value,
                confidence=item.current_occupancy.confidence,
                observed_at=item.current_occupancy.observed_at,
                event_id=item.current_occupancy.event_id,
            ),
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
