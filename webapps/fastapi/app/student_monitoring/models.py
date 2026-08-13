"""Student monitoring domain models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


@dataclass(frozen=True)
class FrameInfo:
    """Frame size information."""

    width_pixels: int
    height_pixels: int


@dataclass(frozen=True)
class Detection:
    """Object detection result."""

    detection_id: str
    class_id: int
    class_name: str
    confidence: float
    bbox: tuple[int, int, int, int]  # [x_min, y_min, x_max, y_max]
    student_id: str | None
    identity_confidence: float | None
    face_bbox: tuple[int, int, int, int] | None


@dataclass(frozen=True)
class DetectionEvent:
    """Detection batch for a single frame."""

    event_id: str
    camera_id: str
    stream_id: str
    classroom_id: str
    captured_at: datetime
    sequence: int
    frame: FrameInfo
    detections: tuple[Detection, ...]
    received_at: datetime
    schema_version: int


@dataclass(frozen=True)
class VideoSegment:
    """Recorder upload completion metadata."""

    segment_id: str
    camera_id: str
    stream_id: str
    classroom_id: str
    recorded_from: datetime
    recorded_to: datetime
    storage: str
    bucket_alias: str
    object_key: str
    size_bytes: int
    received_at: datetime
    schema_version: int


@dataclass(frozen=True)
class DetectionEventPage:
    """Detection event pagination result."""

    items: list[DetectionEvent]
    total: int
    next_cursor: str | None


@dataclass(frozen=True)
class VideoSegmentPage:
    """Video segment pagination result."""

    items: list[VideoSegment]
    total: int


# ============================================================
# 학생 상태 모델
# ============================================================


class StudentState(StrEnum):
    """학생 좌석 상태."""

    PRESENT = "PRESENT"  # 지정 좌석에 있음
    WRONG_SEAT = "WRONG_SEAT"  # 다른 좌석에 있음
    UNKNOWN = "UNKNOWN"  # 학생 미식별 또는 지정 없음


@dataclass(frozen=True)
class StudentSeatState:
    """한 학생의 현재 좌석 상태."""

    student_id: str
    student_name: str
    student_no: str  # 학번
    assigned_seat_id: str | None
    assigned_seat_label: str | None
    current_seat_id: str | None
    current_state: StudentState
    confidence: float | None
    last_observed_at: datetime | None
