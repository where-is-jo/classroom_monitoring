"""ROI 연결 HTTP 스키마."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from pydantic import BaseModel, Field

from .auto_layout import DEFAULT_SEAT_FILL_RATIO, GRID_CORNER_COUNT
from .models import (
    AutoRoiOutcome,
    AutoRoiResult,
    AutoRoiSeatResult,
    ConfirmAutoRoiCommand,
    ConfirmAutoRoiResult,
    GenerateAutoRoiCommand,
    Point,
    ReferenceImage,
    RoiConnectionView,
    SaveLiveRoiConnectionCommand,
    SaveRoiConnectionCommand,
)


class PointSchema(BaseModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class SaveRoiConnectionRequest(BaseModel):
    camera_id: str = Field(min_length=1, max_length=128)
    student_id: str | None = None
    polygon: list[PointSchema] = Field(min_length=3)
    reference_image_revision: int = Field(ge=1)

    def to_command(self, classroom_id: str, seat_id: str) -> SaveRoiConnectionCommand:
        return SaveRoiConnectionCommand(
            classroom_id=classroom_id,
            camera_id=self.camera_id,
            seat_id=seat_id,
            student_id=self.student_id,
            polygon=tuple(Point(x=point.x, y=point.y) for point in self.polygon),
            reference_image_revision=self.reference_image_revision,
        )


class SaveLiveRoiConnectionRequest(BaseModel):
    camera_id: str = Field(min_length=1, max_length=128)
    seat_id: str = Field(min_length=1, max_length=128)
    student_id: str = Field(min_length=1, max_length=128)
    polygon: list[PointSchema] = Field(min_length=3)

    def to_command(self, classroom_id: str) -> SaveLiveRoiConnectionCommand:
        return SaveLiveRoiConnectionCommand(
            classroom_id=classroom_id,
            camera_id=self.camera_id,
            seat_id=self.seat_id,
            student_id=self.student_id,
            polygon=tuple(Point(x=point.x, y=point.y) for point in self.polygon),
        )


class ReferenceImageResponse(BaseModel):
    classroom_id: str
    camera_id: str
    display_name: str
    revision: int
    image_url: str

    @classmethod
    def from_domain(cls, image: ReferenceImage) -> ReferenceImageResponse:
        return cls(
            classroom_id=image.classroom_id,
            camera_id=image.camera_id,
            display_name=image.display_name,
            revision=image.revision,
            image_url=(
                f"/api/v1/classrooms/{image.classroom_id}/roi-reference-image"
                f"?camera_id={quote(image.camera_id, safe='')}"
            ),
        )


class GenerateAutoRoiRequest(BaseModel):
    """좌석 구역 네 모서리에서 좌석 ROI를 만들어 달라는 요청.

    모서리 순서가 격자의 축을 정한다 — 1행 1열 바깥 모서리에서 시작해 이웃한 순서로
    네 곳을 찍는다. 잘못 찍으면 미리보기에서 좌석이 뒤집혀 보이므로 저장 전에 알 수 있다.
    """

    camera_id: str = Field(min_length=1, max_length=128)
    corners: list[PointSchema] = Field(min_length=GRID_CORNER_COUNT, max_length=GRID_CORNER_COUNT)
    reference_image_revision: int = Field(ge=1)
    seat_fill_ratio: float = Field(default=DEFAULT_SEAT_FILL_RATIO, gt=0.2, le=1.0)
    dry_run: bool = False

    def to_command(self, classroom_id: str) -> GenerateAutoRoiCommand:
        return GenerateAutoRoiCommand(
            classroom_id=classroom_id,
            camera_id=self.camera_id,
            corners=tuple(Point(x=corner.x, y=corner.y) for corner in self.corners),
            reference_image_revision=self.reference_image_revision,
            seat_fill_ratio=self.seat_fill_ratio,
            dry_run=self.dry_run,
        )


class ConfirmAutoRoiRequest(BaseModel):
    """자동 생성분을 좌석 판정에 쓰겠다고 확정한다."""

    camera_id: str = Field(min_length=1, max_length=128)
    seat_ids: list[str] | None = Field(default=None, min_length=1)

    def to_command(self, classroom_id: str) -> ConfirmAutoRoiCommand:
        return ConfirmAutoRoiCommand(
            classroom_id=classroom_id,
            camera_id=self.camera_id,
            seat_ids=None if self.seat_ids is None else tuple(self.seat_ids),
        )


class AutoRoiSeatResponse(BaseModel):
    seat_id: str
    seat_label: str
    outcome: AutoRoiOutcome
    polygon: list[PointSchema] | None

    @classmethod
    def from_domain(cls, value: AutoRoiSeatResult) -> AutoRoiSeatResponse:
        return cls(
            seat_id=value.seat_id,
            seat_label=value.seat_label,
            outcome=value.outcome,
            polygon=(
                None
                if value.polygon is None
                else [PointSchema(x=point.x, y=point.y) for point in value.polygon]
            ),
        )


class AutoRoiResponse(BaseModel):
    classroom_id: str
    camera_id: str
    dry_run: bool
    grid_rows: int
    grid_columns: int
    seat_fill_ratio: float
    reference_image_revision: int
    generated_count: int
    skipped_count: int
    seats: list[AutoRoiSeatResponse]

    @classmethod
    def from_domain(cls, value: AutoRoiResult) -> AutoRoiResponse:
        return cls(
            classroom_id=value.classroom_id,
            camera_id=value.camera_id,
            dry_run=value.dry_run,
            grid_rows=value.grid_rows,
            grid_columns=value.grid_columns,
            seat_fill_ratio=value.seat_fill_ratio,
            reference_image_revision=value.reference_image_revision,
            generated_count=value.generated_count,
            skipped_count=value.skipped_count,
            seats=[AutoRoiSeatResponse.from_domain(seat) for seat in value.seats],
        )


class ConfirmAutoRoiResponse(BaseModel):
    confirmed_count: int
    stale_count: int

    @classmethod
    def from_domain(cls, value: ConfirmAutoRoiResult) -> ConfirmAutoRoiResponse:
        return cls(confirmed_count=value.confirmed_count, stale_count=value.stale_count)


class RoiConnectionResponse(BaseModel):
    classroom_id: str
    camera_id: str | None
    seat_id: str
    student_id: str | None
    polygon: list[PointSchema]
    reference_image_revision: int
    needs_review: bool
    auto_generated: bool
    updated_at: datetime

    @classmethod
    def from_domain(cls, view: RoiConnectionView) -> RoiConnectionResponse:
        value = view.connection
        return cls(
            classroom_id=value.classroom_id,
            camera_id=value.camera_id,
            seat_id=value.seat_id,
            student_id=value.student_id,
            polygon=[PointSchema(x=point.x, y=point.y) for point in value.polygon],
            reference_image_revision=value.reference_image_revision,
            needs_review=view.needs_review,
            auto_generated=value.auto_generated,
            updated_at=value.updated_at,
        )


class RoiConnectionListResponse(BaseModel):
    items: list[RoiConnectionResponse]
