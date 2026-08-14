"""ROI 연결 HTTP 스키마."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .models import (
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
    student_id: str | None = None
    polygon: list[PointSchema] = Field(min_length=3)
    reference_image_revision: int = Field(ge=1)

    def to_command(self, classroom_id: str, seat_id: str) -> SaveRoiConnectionCommand:
        return SaveRoiConnectionCommand(
            classroom_id=classroom_id,
            seat_id=seat_id,
            student_id=self.student_id,
            polygon=tuple(Point(x=point.x, y=point.y) for point in self.polygon),
            reference_image_revision=self.reference_image_revision,
        )


class SaveLiveRoiConnectionRequest(BaseModel):
    seat_id: str = Field(min_length=1, max_length=128)
    student_id: str = Field(min_length=1, max_length=128)
    polygon: list[PointSchema] = Field(min_length=3)

    def to_command(self, classroom_id: str) -> SaveLiveRoiConnectionCommand:
        return SaveLiveRoiConnectionCommand(
            classroom_id=classroom_id,
            seat_id=self.seat_id,
            student_id=self.student_id,
            polygon=tuple(Point(x=point.x, y=point.y) for point in self.polygon),
        )


class ReferenceImageResponse(BaseModel):
    classroom_id: str
    display_name: str
    revision: int
    image_url: str

    @classmethod
    def from_domain(cls, image: ReferenceImage) -> ReferenceImageResponse:
        return cls(
            classroom_id=image.classroom_id,
            display_name=image.display_name,
            revision=image.revision,
            image_url=f"/api/v1/classrooms/{image.classroom_id}/roi-reference-image",
        )


class RoiConnectionResponse(BaseModel):
    classroom_id: str
    seat_id: str
    student_id: str | None
    polygon: list[PointSchema]
    reference_image_revision: int
    needs_review: bool
    updated_at: datetime

    @classmethod
    def from_domain(cls, view: RoiConnectionView) -> RoiConnectionResponse:
        value = view.connection
        return cls(
            classroom_id=value.classroom_id,
            seat_id=value.seat_id,
            student_id=value.student_id,
            polygon=[PointSchema(x=point.x, y=point.y) for point in value.polygon],
            reference_image_revision=value.reference_image_revision,
            needs_review=view.needs_review,
            updated_at=value.updated_at,
        )


class RoiConnectionListResponse(BaseModel):
    items: list[RoiConnectionResponse]
