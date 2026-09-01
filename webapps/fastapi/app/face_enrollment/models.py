"""얼굴 등록 도메인 값."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class EnrollmentStatus(StrEnum):
    CREATED = "CREATED"
    FACE_SEARCH = "FACE_SEARCH"
    GUIDING = "GUIDING"
    CAPTURING = "CAPTURING"
    FINAL_VALIDATION = "FINAL_VALIDATION"
    COMPLETE = "COMPLETE"
    ABORTED = "ABORTED"


class PoseBin(StrEnum):
    FRONT = "FRONT"
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    UP = "UP"
    DOWN = "DOWN"


@dataclass(frozen=True)
class PoseProgress:
    pose: PoseBin
    accepted_count: int
    required_count: int


@dataclass(frozen=True)
class FaceEnrollment:
    id: str
    student_id: str
    status: EnrollmentStatus
    consent_confirmed_by: str
    consent_confirmed_at: datetime
    valid_sample_count: int
    required_sample_count: int
    pose_progress: tuple[PoseProgress, ...]
    guidance_code: str
    guidance_message: str
    last_rejection_code: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True)
class FaceProfile:
    student_id: str
    enrollment_id: str
    sample_count: int
    model_version: str
    registered_at: datetime


@dataclass(frozen=True)
class FaceAnalysis:
    face_count: int
    detection_confidence: float
    face_size_ratio: float
    centered: bool
    yaw_degrees: float
    pitch_degrees: float
    roll_degrees: float
    blur_score: float
    brightness_score: float
    landmark_confidence: float
    occlusion_score: float
    duplicate_score: float
    motion_speed_dps: float


@dataclass(frozen=True)
class FaceSampleMetadata:
    sample_id: str
    pose: PoseBin
    captured_at: datetime
    analysis: FaceAnalysis


@dataclass(frozen=True)
class FrameDecision:
    enrollment: FaceEnrollment
    accepted: bool
    rejection_code: str | None


@dataclass(frozen=True)
class CreateEnrollmentCommand:
    student_id: str
    consent_confirmed: bool
    consent_confirmed_by: str
