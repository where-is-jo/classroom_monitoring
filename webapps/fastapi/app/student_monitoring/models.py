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
    """학생 좌석 상태.

    `ABSENT`는 유예 시간을 넘겨 **지정 좌석이 비어 있는 것을 계속 본** 경우에만 쓴다.
    카메라 장애·가림·ROI 미등록 같은 미관측은 `UNKNOWN`이다(결정 0008).
    """

    PRESENT = "PRESENT"  # 지정 좌석에 있음
    WRONG_SEAT = "WRONG_SEAT"  # 다른 좌석에 있음
    IN_CLASSROOM = "IN_CLASSROOM"  # 신원은 확인됐으나 어느 좌석 ROI에도 없음
    ABSENT = "ABSENT"  # 지정 좌석이 유예 시간을 넘겨 비어 있음
    UNKNOWN = "UNKNOWN"  # 판정할 근거가 없음


class StudentStateReason(StrEnum):
    """상태를 그렇게 정한 근거.

    출결은 사람에게 불이익을 줄 수 있는 판정이라 되짚을 수 있어야 한다(결정 0008).
    상태만으로는 "왜 UNKNOWN인가"를 구분할 수 없어 근거 코드를 함께 남긴다.
    """

    IDENTIFIED_AT_ASSIGNED_SEAT = "IDENTIFIED_AT_ASSIGNED_SEAT"
    IDENTIFIED_AT_OTHER_SEAT = "IDENTIFIED_AT_OTHER_SEAT"
    IDENTIFIED_OUTSIDE_SEATS = "IDENTIFIED_OUTSIDE_SEATS"
    IDENTITY_HELD = "IDENTITY_HELD"  # 직전 식별을 유지 시간 안에서 이어받음
    SEAT_OCCUPIED_BY_UNKNOWN = "SEAT_OCCUPIED_BY_UNKNOWN"  # 지정 좌석에 누군가 있으나 미식별
    SEAT_VACANT_WITHIN_GRACE = "SEAT_VACANT_WITHIN_GRACE"  # 비었지만 유예 시간 안
    SEAT_VACANT_BEYOND_GRACE = "SEAT_VACANT_BEYOND_GRACE"  # 비어 있고 유예 시간을 넘김
    SEAT_NOT_OBSERVED = "SEAT_NOT_OBSERVED"  # 지정 좌석을 이번에 보지 못함
    NO_ASSIGNED_SEAT = "NO_ASSIGNED_SEAT"


@dataclass(frozen=True)
class SeatEvidence:
    """한 프레임이 좌석 하나에 대해 만든 근거.

    좌석 점유와 학생 상태가 **같은 근거**를 쓰게 하는 값이다. 예전에는 좌석은 관측
    batch를, 학생은 원본 탐지를 각자 훑어 서로 어긋났다(결정 0020의 남은 일).

    `student_id`는 그 좌석에 매칭된 탐지가 들고 온 신원이다. 얼굴 인식 모델이 붙기
    전까지는 항상 `None`이고, 그때는 좌석 점유만 판정된다.
    """

    seat_id: str
    occupied: bool
    confidence: float
    student_id: str | None
    identity_confidence: float | None


@dataclass(frozen=True)
class StudentSeatState:
    """한 학생의 현재 좌석 상태 (화면·API 표시용)."""

    student_id: str
    student_name: str
    student_no: str  # 학번
    assigned_seat_id: str | None
    assigned_seat_label: str | None
    current_seat_id: str | None
    current_seat_label: str | None
    current_state: StudentState
    reason: StudentStateReason
    confidence: float | None
    last_observed_at: datetime | None


@dataclass(frozen=True)
class StudentStateRecord:
    """저장되는 학생 상태.

    탐지 이벤트를 받을 때만 만들어지고, 조회는 이 값을 읽기만 한다(결정 0008).
    """

    student_id: str
    classroom_id: str
    state: StudentState
    reason: StudentStateReason
    seat_id: str | None  # 지금 있다고 판단한 좌석
    assigned_seat_id: str | None
    confidence: float | None  # 신원 신뢰도
    observed_at: datetime  # 판정 근거가 된 프레임의 촬영 시각
    event_id: str
    identified_at: datetime | None  # 마지막으로 신원을 실제로 확인한 시각
    vacant_since: datetime | None  # 지정 좌석이 비어 보이기 시작한 시각 (유예 계산용)


@dataclass(frozen=True)
class StudentStateHistory:
    """상태가 바뀐 순간의 근거 기록."""

    id: str
    student_id: str
    classroom_id: str
    event_id: str
    from_state: StudentState
    to_state: StudentState
    reason: StudentStateReason
    seat_id: str | None
    confidence: float | None
    observed_at: datetime
    recorded_at: datetime
