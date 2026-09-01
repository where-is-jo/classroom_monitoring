from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.face_enrollment.adapters.memory import (
    InMemoryFaceEnrollmentRepository,
    InMemoryFaceObjectStorage,
    SyntheticFaceAnalyzer,
)
from app.face_enrollment.errors import ConsentRequiredError, EnrollmentConflictError
from app.face_enrollment.models import (
    CreateEnrollmentCommand,
    EnrollmentStatus,
    FaceAnalysis,
    PoseBin,
)
from app.face_enrollment.rules import EnrollmentThresholds, classify_pose, rejection_code
from app.face_enrollment.service import FaceEnrollmentService


def make_thresholds() -> EnrollmentThresholds:
    return EnrollmentThresholds(
        detection_confidence_min=0.8,
        face_size_ratio_min=0.1,
        roll_degrees_max=10,
        blur_score_min=0.5,
        brightness_score_min=0.4,
        landmark_confidence_min=0.8,
        occlusion_score_max=0.3,
        duplicate_score_max=0.9,
        motion_speed_dps_max=240,
        yaw_side_degrees=15,
        pitch_side_degrees=10,
        pitch_down_degrees=5,
    )


def make_service(*, required: int = 5) -> FaceEnrollmentService:
    return FaceEnrollmentService(
        InMemoryFaceEnrollmentRepository(),
        InMemoryFaceObjectStorage(),
        SyntheticFaceAnalyzer(),
        required_sample_count=required,
        augmented_sample_count=0,
        pose_quotas=dict.fromkeys(PoseBin, 1),
        thresholds=make_thresholds(),
        clock=lambda: datetime(2026, 8, 12, tzinfo=UTC),
    )


def create(service: FaceEnrollmentService) -> str:
    return service.create(
        CreateEnrollmentCommand(
            student_id="student-1",
            consent_confirmed=True,
            consent_confirmed_by="admin",
        )
    ).id


def test_requires_consent_confirmation() -> None:
    service = make_service()
    with pytest.raises(ConsentRequiredError):
        service.create(CreateEnrollmentCommand("student-1", False, "admin"))


def test_only_one_active_enrollment_is_allowed() -> None:
    service = make_service()
    create(service)
    with pytest.raises(EnrollmentConflictError):
        create(service)


def test_completes_only_after_count_and_all_pose_quotas() -> None:
    service = make_service()
    enrollment_id = create(service)
    for _ in range(4):
        decision = service.process_frame(enrollment_id, b"jpeg")
        assert decision.enrollment.status != EnrollmentStatus.COMPLETE
    decision = service.process_frame(enrollment_id, b"jpeg")
    assert decision.enrollment.status == EnrollmentStatus.COMPLETE
    assert decision.enrollment.valid_sample_count == 5
    profile = service.get_profile("student-1")
    assert profile is not None
    assert profile.sample_count == 5


def test_rejected_frame_does_not_increment_count() -> None:
    service = make_service()
    enrollment_id = create(service)
    decision = service.process_frame(enrollment_id, b"NO_FACE")
    assert not decision.accepted
    assert decision.rejection_code == "FACE_NOT_FOUND"
    assert decision.enrollment.valid_sample_count == 0


@pytest.mark.parametrize(
    ("marker", "code"),
    [
        (b"BLUR", "BLURRY"),
        (b"DARK", "BAD_LIGHTING"),
        (b"OCCLUDED", "OCCLUDED"),
        (b"DUPLICATE", "DUPLICATE"),
        (b"FAST", "MOVING_TOO_FAST"),
    ],
)
def test_unusable_quality_frame_is_not_stored(marker: bytes, code: str) -> None:
    service = make_service()
    enrollment_id = create(service)

    decision = service.process_frame(enrollment_id, marker)

    assert not decision.accepted
    assert decision.rejection_code == code
    assert decision.enrollment.valid_sample_count == 0


def test_valid_pose_is_collected_without_sequential_direction_gate() -> None:
    service = make_service()
    enrollment_id = create(service)
    right = service.process_frame(enrollment_id, b"RIGHT")
    assert right.accepted
    assert right.rejection_code is None
    assert right.enrollment.guidance_code == "LOOK_FRONT"
    assert "정면" in right.enrollment.guidance_message
    assert "장" not in right.enrollment.guidance_message
    assert right.enrollment.valid_sample_count == 1
    right_progress = next(
        item for item in right.enrollment.pose_progress if item.pose == PoseBin.RIGHT
    )
    assert right_progress.accepted_count == 1

    up = service.process_frame(enrollment_id, b"UP")
    assert up.accepted
    up_progress = next(item for item in up.enrollment.pose_progress if item.pose == PoseBin.UP)
    assert up_progress.accepted_count == 1


def test_stored_sample_keeps_masked_frame_and_uses_readable_name() -> None:
    repository = InMemoryFaceEnrollmentRepository()
    storage = InMemoryFaceObjectStorage()
    service = FaceEnrollmentService(
        repository,
        storage,
        SyntheticFaceAnalyzer(),
        required_sample_count=5,
        augmented_sample_count=0,
        pose_quotas=dict.fromkeys(PoseBin, 1),
        thresholds=make_thresholds(),
        clock=lambda: datetime(2026, 8, 12, tzinfo=UTC),
    )
    enrollment_id = service.create(CreateEnrollmentCommand("student 01", True, "admin")).id

    service.process_frame(enrollment_id, b"RIGHT-aligned-jpeg")

    assert storage._samples[enrollment_id] == {"student-01_right_000001": b"RIGHT-aligned-jpeg"}

    assert storage.read_originals(
        enrollment_id=enrollment_id,
        student_id="student 01",
        student_number="ST-001",
    ) == [b"RIGHT-aligned-jpeg"]
    assert (
        storage.read_originals(
            enrollment_id=enrollment_id,
            student_id="another-student",
            student_number="ST-002",
        )
        == []
    )


def test_filled_pose_is_paused_until_other_pose_quotas_are_complete() -> None:
    service = make_service()
    enrollment_id = create(service)
    first = service.process_frame(enrollment_id, b"RIGHT")
    second = service.process_frame(enrollment_id, b"RIGHT")

    assert first.accepted
    assert not second.accepted
    assert second.rejection_code == "POSE_QUOTA_FILLED"
    assert second.enrollment.valid_sample_count == 1


def test_natural_roll_during_side_pose_is_not_rejected() -> None:
    analysis = FaceAnalysis(
        face_count=1,
        detection_confidence=1,
        face_size_ratio=0.2,
        centered=True,
        yaw_degrees=25,
        pitch_degrees=0,
        roll_degrees=20,
        blur_score=1,
        brightness_score=1,
        landmark_confidence=1,
        occlusion_score=0,
        duplicate_score=0,
        motion_speed_dps=0,
    )

    assert rejection_code(analysis, make_thresholds()) is None


def test_camera_yaw_is_mirrored_to_user_direction() -> None:
    camera_right = FaceAnalysis(
        face_count=1,
        detection_confidence=1,
        face_size_ratio=0.2,
        centered=True,
        yaw_degrees=25,
        pitch_degrees=0,
        roll_degrees=0,
        blur_score=1,
        brightness_score=1,
        landmark_confidence=1,
        occlusion_score=0,
        duplicate_score=0,
        motion_speed_dps=0,
    )

    assert classify_pose(camera_right, make_thresholds()) == PoseBin.LEFT


def test_down_pose_uses_more_permissive_threshold_than_up_pose() -> None:
    base = FaceAnalysis(
        face_count=1,
        detection_confidence=1,
        face_size_ratio=0.2,
        centered=True,
        yaw_degrees=0,
        pitch_degrees=6,
        roll_degrees=0,
        blur_score=1,
        brightness_score=1,
        landmark_confidence=1,
        occlusion_score=0,
        duplicate_score=0,
        motion_speed_dps=0,
    )
    upward = replace(base, pitch_degrees=-6)

    assert classify_pose(base, make_thresholds()) == PoseBin.DOWN
    assert classify_pose(upward, make_thresholds()) == PoseBin.FRONT


def test_abort_removes_active_session() -> None:
    service = make_service()
    first = create(service)
    service.process_frame(first, b"jpeg")
    service.abort(first)
    second = create(service)
    assert second != first
