"""Student monitoring HTTP schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import DetectionEvent, VideoSegment


class FrameSchema(BaseModel):
    """Frame size schema."""

    model_config = ConfigDict(extra="forbid")

    width_pixels: int = Field(..., gt=0)
    height_pixels: int = Field(..., gt=0)


class DetectionSchema(BaseModel):
    """Detection result schema."""

    model_config = ConfigDict(extra="forbid")

    detection_id: str
    class_id: int
    class_name: str
    confidence: float = Field(..., ge=0, le=1)
    bbox: tuple[int, int, int, int]
    student_id: str | None = None
    identity_confidence: float | None = Field(default=None, ge=0, le=1)
    face_bbox: tuple[int, int, int, int] | None = None
    # 결정 0025의 6번이 정한 필드 추가. worker가 트래킹을 붙이면 그대로 채워 보낸다.
    track_id: str | None = None

    @field_validator("bbox", "face_bbox")
    @classmethod
    def validate_bbox(cls, v: tuple[int, int, int, int] | None) -> tuple[int, int, int, int] | None:
        if v is None:
            return None
        x_min, y_min, x_max, y_max = v
        if x_min >= x_max:
            raise ValueError("x_min must be less than x_max")
        if y_min >= y_max:
            raise ValueError("y_min must be less than y_max")
        if x_min < 0 or y_min < 0:
            raise ValueError("bbox coordinates must be non-negative")
        return v

    @model_validator(mode="after")
    def validate_identity_fields(self) -> DetectionSchema:
        has_student = self.student_id is not None
        has_confidence = self.identity_confidence is not None
        if has_student != has_confidence:
            raise ValueError("student_id and identity_confidence must be provided together")
        if self.face_bbox is not None and not has_student:
            raise ValueError("face_bbox requires identified student fields")
        return self


class InferenceEventRequest(BaseModel):
    """Inference event request schema."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    camera_id: str
    captured_at: datetime
    sequence: int = Field(..., ge=0)
    frame: FrameSchema
    detections: list[DetectionSchema] = Field(
        default_factory=list,
        max_length=100,  # Will be overridden by settings
    )

    @field_validator("captured_at")
    @classmethod
    def validate_timezone(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("captured_at must have timezone")
        return v

    @model_validator(mode="after")
    def validate_detection_coordinates(self) -> InferenceEventRequest:
        for detection in self.detections:
            for field_name, bbox in (("bbox", detection.bbox), ("face_bbox", detection.face_bbox)):
                if bbox is not None and (
                    bbox[2] > self.frame.width_pixels or bbox[3] > self.frame.height_pixels
                ):
                    raise ValueError(f"{field_name} must stay within the frame")
        return self


class InferenceEventResponse(BaseModel):
    """Inference event response schema."""

    event_id: str
    received_at: datetime


class VideoSegmentRequest(BaseModel):
    """Video segment request schema."""

    segment_id: str
    camera_id: str
    recorded_from: datetime
    recorded_to: datetime
    storage: str
    bucket_alias: str
    object_key: str
    size_bytes: int = Field(..., ge=0)

    @field_validator("recorded_from", "recorded_to")
    @classmethod
    def validate_timezone(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("Timestamp must have timezone")
        return v


class VideoSegmentResponse(BaseModel):
    """Video segment response schema."""

    segment_id: str
    received_at: datetime


class DetectionResponse(BaseModel):
    """Detection response for public API."""

    detection_id: str
    class_id: int
    class_name: str
    confidence: float
    bbox: tuple[int, int, int, int]
    student_id: str | None = None
    # 같은 카메라에서 연속 관측된 사람인지 API 소비자가 확인할 수 있게 한다.
    # 얼굴 유사도 같은 민감한 판정 근거는 공개하지 않고 worker가 만든 track 식별자만 싣는다.
    track_id: str | None = None


class DetectionEventResponse(BaseModel):
    """Detection event response for public API."""

    event_id: str
    camera_id: str
    captured_at: datetime
    sequence: int
    frame: FrameSchema
    detections: list[DetectionResponse]
    received_at: datetime

    @classmethod
    def from_domain(cls, event: DetectionEvent) -> DetectionEventResponse:
        return cls(
            event_id=event.event_id,
            camera_id=event.camera_id,
            captured_at=event.captured_at,
            sequence=event.sequence,
            frame=FrameSchema(
                width_pixels=event.frame.width_pixels,
                height_pixels=event.frame.height_pixels,
            ),
            detections=[
                DetectionResponse(
                    detection_id=d.detection_id,
                    class_id=d.class_id,
                    class_name=d.class_name,
                    confidence=d.confidence,
                    bbox=d.bbox,
                    student_id=d.student_id,
                    track_id=d.track_id,
                )
                for d in event.detections
            ],
            received_at=event.received_at,
        )


class DetectionEventListResponse(BaseModel):
    """Detection event list response."""

    items: list[DetectionEventResponse]
    total: int
    next_cursor: str | None = None


class VideoSegmentDetailResponse(BaseModel):
    """Video segment detail response."""

    segment_id: str
    camera_id: str
    recorded_from: datetime
    recorded_to: datetime
    storage: str
    bucket_alias: str
    object_key: str
    size_bytes: int
    received_at: datetime

    @classmethod
    def from_domain(cls, segment: VideoSegment) -> VideoSegmentDetailResponse:
        return cls(
            segment_id=segment.segment_id,
            camera_id=segment.camera_id,
            recorded_from=segment.recorded_from,
            recorded_to=segment.recorded_to,
            storage=segment.storage,
            bucket_alias=segment.bucket_alias,
            object_key=segment.object_key,
            size_bytes=segment.size_bytes,
            received_at=segment.received_at,
        )


class VideoSegmentListResponse(BaseModel):
    """Video segment list response."""

    items: list[VideoSegmentDetailResponse]
    total: int


# ============================================================
# 학생 상태 스키마
# ============================================================


class StudentSeatStateResponse(BaseModel):
    """학생 좌석 상태 응답.

    `current_seat_id`·`current_seat_label`·`reason`은 필드 추가이며 기존 필드를
    지우거나 바꾸지 않는다. `WRONG_SEAT`일 때 어디에 앉았는지, `UNKNOWN`일 때 왜
    모르는지를 화면이 스스로 판단하지 않고 그대로 받게 하려는 것이다.
    """

    student_id: str
    student_name: str
    student_no: str  # 학번
    assigned_seat_id: str | None
    assigned_seat_label: str | None
    current_seat_id: str | None
    current_seat_label: str | None
    current_state: str
    reason: str
    confidence: float | None
    last_observed_at: datetime | None


class StudentStateListResponse(BaseModel):
    """학생 상태 목록 응답."""

    classroom_id: str
    states: list[StudentSeatStateResponse]


class StudentStateHistoryItemResponse(BaseModel):
    """학생 상태 전이 이력 한 건."""

    event_id: str
    from_state: str
    to_state: str
    reason: str
    seat_id: str | None
    confidence: float | None
    observed_at: datetime


class StudentStateHistoryResponse(BaseModel):
    """학생 상태 전이 이력 목록 응답."""

    classroom_id: str
    student_id: str
    items: list[StudentStateHistoryItemResponse]
    total: int
