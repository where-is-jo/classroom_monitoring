"""입구 얼굴 관측 이벤트의 HTTP 요청·응답 스키마."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import (
    EntryFaceObservation,
    EntryFrameInfo,
    EntryIdentityEvent,
    EntryIdentityEventPage,
    EntryIdentityProcessingStatus,
    EntryIdentityStatus,
)


class EntryFrameSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    width_pixels: int = Field(ge=1, le=16384)
    height_pixels: int = Field(ge=1, le=16384)

    def to_domain(self) -> EntryFrameInfo:
        return EntryFrameInfo(self.width_pixels, self.height_pixels)


class EntryFaceObservationSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    face_track_id: str = Field(min_length=1, max_length=128)
    face_bbox: tuple[int, int, int, int]
    detection_confidence: float = Field(ge=0, le=1)
    identity_status: EntryIdentityStatus
    student_id: str | None = Field(default=None, min_length=1, max_length=128)
    similarity: float | None = Field(default=None, ge=-1, le=1)
    margin: float | None = Field(default=None, ge=0, le=2)
    quality: float = Field(ge=0, le=1)
    observation_count: int = Field(ge=0)
    rejected_reason: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_identity_fields(self) -> EntryFaceObservationSchema:
        if (self.similarity is None) != (self.margin is None):
            raise ValueError("similarity와 margin은 함께 있거나 함께 없어야 합니다.")
        if self.identity_status is EntryIdentityStatus.REGISTERED:
            if self.student_id is None or self.similarity is None:
                raise ValueError("REGISTERED 관측에는 학생 ID와 식별 근거가 필요합니다.")
            if self.rejected_reason is not None:
                raise ValueError("REGISTERED 관측에는 거절 사유를 둘 수 없습니다.")
        elif self.student_id is not None:
            raise ValueError("미식별 관측에는 학생 ID를 둘 수 없습니다.")
        return self

    def to_domain(self) -> EntryFaceObservation:
        return EntryFaceObservation(
            face_track_id=self.face_track_id,
            face_bbox=self.face_bbox,
            detection_confidence=self.detection_confidence,
            identity_status=self.identity_status,
            student_id=self.student_id,
            similarity=self.similarity,
            margin=self.margin,
            quality=self.quality,
            observation_count=self.observation_count,
            rejected_reason=self.rejected_reason,
        )


class EntryIdentityEventCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=256)
    camera_id: str = Field(min_length=1, max_length=128)
    captured_at: datetime
    sequence: int = Field(ge=0)
    frame: EntryFrameSchema
    processing_status: EntryIdentityProcessingStatus
    observations: list[EntryFaceObservationSchema] = Field(max_length=100)

    @field_validator("captured_at")
    @classmethod
    def captured_at_requires_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("captured_at에는 timezone이 필요합니다.")
        return value

    @model_validator(mode="after")
    def validate_frame_contract(self) -> EntryIdentityEventCreateRequest:
        if (
            self.processing_status is not EntryIdentityProcessingStatus.SUCCEEDED
            and self.observations
        ):
            raise ValueError("실패 처리 상태에는 얼굴 관측을 둘 수 없습니다.")
        track_ids = [item.face_track_id for item in self.observations]
        if len(track_ids) != len(set(track_ids)):
            raise ValueError("한 프레임의 face_track_id는 중복될 수 없습니다.")
        for observation in self.observations:
            left, top, right, bottom = observation.face_bbox
            if not (
                0 <= left < right <= self.frame.width_pixels
                and 0 <= top < bottom <= self.frame.height_pixels
            ):
                raise ValueError("face_bbox는 프레임 안의 유효한 사각형이어야 합니다.")
        return self


class EntryIdentityEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: str
    camera_id: str
    stream_id: str
    captured_at: datetime
    sequence: int
    frame: EntryFrameSchema
    processing_status: EntryIdentityProcessingStatus
    observations: list[EntryFaceObservationSchema]
    received_at: datetime
    expires_at: datetime

    @classmethod
    def from_domain(cls, event: EntryIdentityEvent) -> EntryIdentityEventResponse:
        return cls.model_validate(event)


class EntryIdentityEventPageResponse(BaseModel):
    items: list[EntryIdentityEventResponse]
    total: int
    next_cursor: str | None

    @classmethod
    def from_domain(cls, page: EntryIdentityEventPage) -> EntryIdentityEventPageResponse:
        return cls(
            items=[EntryIdentityEventResponse.from_domain(item) for item in page.items],
            total=page.total,
            next_cursor=page.next_cursor,
        )
