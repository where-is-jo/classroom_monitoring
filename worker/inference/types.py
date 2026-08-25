"""추론 입출력 타입.

Frame은 워커마다 따로 정의하지 않고 shared에서 가져온다. 같은 것에 이름이
둘 생기면 stream이 넘긴 배열과 inference가 기대하는 배열이 같은 것인지
타입만 보고 알 수 없다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from shared.types import Frame

BBox = tuple[int, int, int, int]

__all__ = [
    "BBox",
    "Detection",
    "EntryFaceObservation",
    "EntryFaceObservationBatch",
    "EntryIdentityProcessingStatus",
    "EntryIdentityStatus",
    "Frame",
    "InferenceResult",
]


class EntryIdentityStatus(StrEnum):
    REGISTERED = "REGISTERED"
    UNKNOWN = "UNKNOWN"
    UNCERTAIN = "UNCERTAIN"


class EntryIdentityProcessingStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    ANALYZER_UNAVAILABLE = "ANALYZER_UNAVAILABLE"
    INVALID_RESPONSE = "INVALID_RESPONSE"


@dataclass(frozen=True)
class EntryFaceObservation:
    """입구 얼굴 서비스가 만든 한 얼굴 track의 관측값."""

    face_track_id: str
    face_bbox: BBox
    detection_confidence: float
    identity_status: EntryIdentityStatus
    student_id: str | None
    similarity: float | None
    margin: float | None
    quality: float
    observation_count: int
    rejected_reason: str | None


@dataclass(frozen=True)
class EntryFaceObservationBatch:
    """입구 프레임 한 장의 얼굴 관측. 일반 사람 Detection과 섞지 않는다."""

    frame_shape: tuple[int, int, int]
    processing_status: EntryIdentityProcessingStatus
    observations: tuple[EntryFaceObservation, ...]


@dataclass(frozen=True)
class Detection:
    class_id: int
    class_name: str
    confidence: float
    bbox: BBox
    # 입구 얼굴 신원이 CCTV track에 인계되면 채우는 선택 필드다. 사람 detector는
    # 기본값을 그대로 사용한다. 학생 이름·좌석 상태는 이 경계에 두지 않는다.
    student_id: str | None = None
    identity_confidence: float | None = None
    face_bbox: BBox | None = None
    # 같은 카메라 안에서 같은 사람을 이어 보는 식별자 (결정 0025·0036).
    # worker의 CCTV ByteTrack이 채우고, fastapi는 그대로 받아 저장한다.
    track_id: str | None = None


@dataclass(frozen=True)
class InferenceResult:
    frame_shape: tuple[int, int, int]
    detections: tuple[Detection, ...]
