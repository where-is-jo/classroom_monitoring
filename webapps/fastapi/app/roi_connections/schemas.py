"""ROI 연결 HTTP 스키마."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import quote

from pydantic import BaseModel, Field

from .models import (
    ApplyDetectionRoiCommand,
    ApplyDetectionRoiResult,
    ConfirmAutoRoiCommand,
    ConfirmAutoRoiResult,
    DetectionRoiAssignment,
    DetectionRoiPlanResult,
    DetectionRoiProposal,
    PlanDetectionRoiCommand,
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


class PlanDetectionRoiRequest(BaseModel):
    """카메라가 실제로 본 것에서 좌석 자리를 찾아 달라는 요청.

    좌석을 지정하지 않는다 — 어느 자리가 어느 좌석인지는 카메라가 알 수 없다.
    """

    camera_id: str = Field(min_length=1, max_length=128)
    lookback_hours: int = Field(default=24, ge=1, le=24 * 14)

    def to_command(self, classroom_id: str) -> PlanDetectionRoiCommand:
        return PlanDetectionRoiCommand(
            classroom_id=classroom_id,
            camera_id=self.camera_id,
            lookback_hours=self.lookback_hours,
        )


class DetectionRoiProposalResponse(BaseModel):
    index: int
    polygon: list[PointSchema]
    sample_count: int
    suggested_seat_id: str | None

    @classmethod
    def from_domain(cls, value: DetectionRoiProposal) -> DetectionRoiProposalResponse:
        return cls(
            index=value.index,
            polygon=[PointSchema(x=point.x, y=point.y) for point in value.polygon],
            sample_count=value.sample_count,
            suggested_seat_id=value.suggested_seat_id,
        )


class DetectionRoiPlanResponse(BaseModel):
    classroom_id: str
    camera_id: str
    window_from: datetime
    window_to: datetime
    sample_count: int
    stationary_count: int
    dropped_overlapping: int
    dropped_weak: int
    proposals: list[DetectionRoiProposalResponse]

    @classmethod
    def from_domain(cls, value: DetectionRoiPlanResult) -> DetectionRoiPlanResponse:
        return cls(
            classroom_id=value.classroom_id,
            camera_id=value.camera_id,
            window_from=value.window_from,
            window_to=value.window_to,
            sample_count=value.sample_count,
            stationary_count=value.stationary_count,
            dropped_overlapping=value.dropped_overlapping,
            dropped_weak=value.dropped_weak,
            proposals=[DetectionRoiProposalResponse.from_domain(item) for item in value.proposals],
        )


class DetectionRoiAssignmentRequest(BaseModel):
    seat_id: str = Field(min_length=1, max_length=128)
    polygon: list[PointSchema] = Field(min_length=3, max_length=64)


class ApplyDetectionRoiRequest(BaseModel):
    """관리자가 좌석을 지정한 자리들을 저장한다."""

    camera_id: str = Field(min_length=1, max_length=128)
    assignments: list[DetectionRoiAssignmentRequest] = Field(min_length=1, max_length=200)

    def to_command(self, classroom_id: str) -> ApplyDetectionRoiCommand:
        return ApplyDetectionRoiCommand(
            classroom_id=classroom_id,
            camera_id=self.camera_id,
            assignments=tuple(
                DetectionRoiAssignment(
                    seat_id=assignment.seat_id,
                    polygon=tuple(Point(x=point.x, y=point.y) for point in assignment.polygon),
                )
                for assignment in self.assignments
            ),
        )


class ApplyDetectionRoiResponse(BaseModel):
    saved_count: int
    seat_ids: list[str]

    @classmethod
    def from_domain(cls, value: ApplyDetectionRoiResult) -> ApplyDetectionRoiResponse:
        return cls(saved_count=value.saved_count, seat_ids=list(value.seat_ids))
