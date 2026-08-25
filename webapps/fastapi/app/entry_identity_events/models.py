"""입구 얼굴 관측 이벤트의 프레임워크 독립 도메인 모델."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class EntryIdentityStatus(StrEnum):
    REGISTERED = "REGISTERED"
    UNKNOWN = "UNKNOWN"
    UNCERTAIN = "UNCERTAIN"


class EntryIdentityProcessingStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    ANALYZER_UNAVAILABLE = "ANALYZER_UNAVAILABLE"
    INVALID_RESPONSE = "INVALID_RESPONSE"


@dataclass(frozen=True)
class EntryFrameInfo:
    width_pixels: int
    height_pixels: int


@dataclass(frozen=True)
class EntryFaceObservation:
    face_track_id: str
    face_bbox: tuple[int, int, int, int]
    detection_confidence: float
    identity_status: EntryIdentityStatus
    student_id: str | None
    similarity: float | None
    margin: float | None
    quality: float
    observation_count: int
    rejected_reason: str | None


@dataclass(frozen=True)
class EntryIdentityEvent:
    event_id: str
    camera_id: str
    stream_id: str
    captured_at: datetime
    sequence: int
    frame: EntryFrameInfo
    processing_status: EntryIdentityProcessingStatus
    observations: tuple[EntryFaceObservation, ...]
    received_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class EntryIdentityEventPage:
    items: list[EntryIdentityEvent]
    total: int
    next_cursor: str | None


@dataclass(frozen=True)
class EntryIdentityEventSaveResult:
    event: EntryIdentityEvent
    created: bool


def same_event_body(left: EntryIdentityEvent, right: EntryIdentityEvent) -> bool:
    """수신·만료 시각을 제외한 worker 본문과 귀속 stream이 같은지 비교한다."""

    return (
        left.event_id == right.event_id
        and left.camera_id == right.camera_id
        and left.stream_id == right.stream_id
        and left.captured_at == right.captured_at
        and left.sequence == right.sequence
        and left.frame == right.frame
        and left.processing_status == right.processing_status
        and left.observations == right.observations
    )
