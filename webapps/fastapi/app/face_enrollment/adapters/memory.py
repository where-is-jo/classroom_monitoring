"""로컬 개발용 얼굴 등록 저장소와 합성 분석기."""

from __future__ import annotations

from datetime import datetime
from threading import RLock

from ..models import (
    EnrollmentStatus,
    FaceAnalysis,
    FaceEnrollment,
    FaceProfile,
    FaceSampleMetadata,
    PoseBin,
)


class InMemoryFaceEnrollmentRepository:
    def __init__(self) -> None:
        self._enrollments: dict[str, FaceEnrollment] = {}
        self._profiles: dict[str, FaceProfile] = {}
        self._lock = RLock()

    def create(self, enrollment: FaceEnrollment) -> FaceEnrollment:
        with self._lock:
            self._enrollments[enrollment.id] = enrollment
            return enrollment

    def get(self, enrollment_id: str) -> FaceEnrollment | None:
        with self._lock:
            return self._enrollments.get(enrollment_id)

    def get_active(self) -> FaceEnrollment | None:
        with self._lock:
            return next(
                (
                    item
                    for item in self._enrollments.values()
                    if item.status not in {EnrollmentStatus.COMPLETE, EnrollmentStatus.ABORTED}
                ),
                None,
            )

    def replace(self, enrollment: FaceEnrollment) -> FaceEnrollment:
        with self._lock:
            self._enrollments[enrollment.id] = enrollment
            return enrollment

    def delete(self, enrollment_id: str) -> None:
        with self._lock:
            self._enrollments.pop(enrollment_id, None)

    def get_profile(self, student_id: str) -> FaceProfile | None:
        with self._lock:
            return self._profiles.get(student_id)

    def save_profile(self, profile: FaceProfile) -> FaceProfile:
        with self._lock:
            self._profiles[profile.student_id] = profile
            return profile

    def delete_profile(self, student_id: str) -> None:
        with self._lock:
            self._profiles.pop(student_id, None)


class InMemoryFaceObjectStorage:
    def __init__(self) -> None:
        self._samples: dict[str, dict[str, bytes]] = {}
        self._student_ids: dict[str, str] = {}
        self._lock = RLock()

    def prepare_enrollment(self, enrollment_id: str, student_id: str, created_at: datetime) -> None:
        with self._lock:
            self._samples.setdefault(enrollment_id, {})
            self._student_ids[enrollment_id] = student_id

    def put_sample(self, enrollment_id: str, metadata: FaceSampleMetadata, content: bytes) -> None:
        with self._lock:
            self._samples.setdefault(enrollment_id, {})[metadata.sample_id] = content

    def finalize_dataset(
        self, enrollment_id: str, student_id: str, augmented_sample_count: int
    ) -> None:
        return None

    def read_originals(
        self, *, enrollment_id: str, student_id: str, student_number: str
    ) -> list[bytes]:
        """파일 보존을 끈 local 테스트에서도 완료 직후 벡터화를 허용한다."""

        with self._lock:
            if self._student_ids.get(enrollment_id) != student_id:
                return []
            return list(self._samples.get(enrollment_id, {}).values())

    def delete_enrollment(self, enrollment_id: str) -> None:
        with self._lock:
            self._samples.pop(enrollment_id, None)
            self._student_ids.pop(enrollment_id, None)


class SyntheticFaceAnalyzer:
    """모델 없이 API와 UI 흐름을 검증하는 결정론적 분석기."""

    def __init__(self, pose_run_lengths: tuple[tuple[PoseBin, int], ...] | None = None) -> None:
        self._frame_count = 0
        self._lock = RLock()
        self._pose_sequence = tuple(
            pose.value
            for pose, run_length in (pose_run_lengths or tuple((pose, 1) for pose in PoseBin))
            for _ in range(run_length)
        )

    def analyze(self, enrollment_id: str, frame: bytes) -> FaceAnalysis:
        marker = frame[:32].decode("ascii", errors="ignore")
        with self._lock:
            self._frame_count += 1
            synthetic_pose = self._pose_sequence[(self._frame_count - 1) % len(self._pose_sequence)]
        pose = (
            marker
            if any(name in marker for name in ("FRONT", "LEFT", "RIGHT", "UP", "DOWN"))
            else synthetic_pose
        )
        yaw = 25.0 if "LEFT" in pose else -25.0 if "RIGHT" in pose else 0.0
        pitch = -18.0 if "UP" in pose else 18.0 if "DOWN" in pose else 0.0
        return FaceAnalysis(
            face_count=0 if "NO_FACE" in marker else 2 if "MULTI" in marker else 1,
            detection_confidence=0.99,
            face_size_ratio=0.3,
            centered="OFFCENTER" not in marker,
            yaw_degrees=yaw,
            pitch_degrees=pitch,
            roll_degrees=0.0,
            blur_score=0.1 if "BLUR" in marker else 0.9,
            brightness_score=0.1 if "DARK" in marker or "BRIGHT" in marker else 0.9,
            landmark_confidence=0.1 if "NO_LANDMARK" in marker else 0.99,
            occlusion_score=0.9 if "OCCLUDED" in marker else 0.0,
            duplicate_score=1.0 if "DUPLICATE" in marker else 0.0,
            motion_speed_dps=999.0 if "FAST" in marker else 0.0,
        )

    def finalize(self, enrollment_id: str) -> str:
        return "synthetic-adaface-r50-v1"

    def discard(self, enrollment_id: str) -> None:
        return None
