"""능동형 얼굴 등록 업무 흐름."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from uuid import uuid4

from .errors import (
    ConsentRequiredError,
    EnrollmentConflictError,
    EnrollmentFrameError,
    EnrollmentNotFoundError,
)
from .models import (
    CreateEnrollmentCommand,
    EnrollmentStatus,
    FaceEnrollment,
    FaceProfile,
    FaceSampleMetadata,
    FrameDecision,
    PoseBin,
    PoseProgress,
)
from .ports import FaceAnalyzer, FaceEnrollmentRepository, FaceObjectStorage
from .rules import (
    EnrollmentThresholds,
    classify_pose,
    increment_pose,
    pose_quota_complete,
    quotas_complete,
    rejection_code,
)

GUIDANCE_MESSAGES = {
    "FIND_FACE": "얼굴을 타원 안에 맞춰 주세요.",
    "MOVE_SLOWLY": "고개를 천천히 원을 그리듯 자유롭게 움직여 주세요.",
    "FINAL_VALIDATION": "수집한 얼굴 데이터를 최종 확인하고 있습니다.",
    "COMPLETE": "얼굴 등록이 완료되었습니다.",
    "FACE_NOT_FOUND": "얼굴을 타원 안에 맞춰 주세요.",
    "MULTIPLE_FACES": "한 사람만 카메라 앞에 서 주세요.",
    "LOW_DETECTION_CONFIDENCE": "얼굴이 잘 보이도록 자세를 조정해 주세요.",
    "FACE_TOO_SMALL": "카메라에 조금 더 가까이 와 주세요.",
    "FACE_NOT_CENTERED": "얼굴을 화면 중앙에 맞춰 주세요.",
    "HEAD_TILTED": "고개를 기울이지 말고 바르게 해 주세요.",
    "BLURRY": "얼굴이 흐리게 보입니다. 카메라를 바라보고 잠시 멈춰 주세요.",
    "BAD_LIGHTING": "얼굴이 밝게 보이도록 조명을 조정해 주세요.",
    "LANDMARK_UNSTABLE": "눈·코·입이 잘 보이도록 해 주세요.",
    "MOVING_TOO_FAST": "고개를 조금 더 천천히 움직여 주세요.",
    "OCCLUDED": "마스크나 손 등 얼굴을 가리는 물체를 치워 주세요.",
    "DUPLICATE": "좋아요. 조금 더 움직여 다른 각도를 보여 주세요.",
    "EXPLORE_OTHER_ANGLE": "이 각도는 충분합니다. 고개를 천천히 다른 각도로 움직여 주세요.",
}

POSE_GUIDANCE = {
    PoseBin.FRONT: ("LOOK_FRONT", "정면", "정면을 바라보고 잠시 멈춰 주세요."),
    PoseBin.LEFT: ("TURN_LEFT", "왼쪽", "고개를 천천히 왼쪽으로 돌려 주세요."),
    PoseBin.RIGHT: ("TURN_RIGHT", "오른쪽", "고개를 천천히 오른쪽으로 돌려 주세요."),
    PoseBin.UP: ("LOOK_UP", "위", "턱을 조금 들고 위쪽을 바라봐 주세요."),
    PoseBin.DOWN: ("LOOK_DOWN", "아래", "턱을 조금 내리고 아래쪽을 바라봐 주세요."),
}


def _safe_filename_component(value: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z가-힣._-]+", "-", value.strip())
    return normalized.strip(".-_")[:80] or "student"


def _next_pose_guidance(progress: tuple[PoseProgress, ...]) -> tuple[str, str]:
    incomplete = [item for item in progress if item.accepted_count < item.required_count]
    if not incomplete:
        return "FINAL_VALIDATION", GUIDANCE_MESSAGES["FINAL_VALIDATION"]
    target = min(
        incomplete,
        key=lambda item: (
            item.accepted_count / item.required_count,
            tuple(PoseBin).index(item.pose),
        ),
    )
    code, _label, message = POSE_GUIDANCE[target.pose]
    return code, message


class FaceEnrollmentService:
    def __init__(
        self,
        repository: FaceEnrollmentRepository,
        object_storage: FaceObjectStorage,
        analyzer: FaceAnalyzer,
        *,
        required_sample_count: int,
        augmented_sample_count: int,
        pose_quotas: dict[PoseBin, int],
        thresholds: EnrollmentThresholds,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._object_storage = object_storage
        self._analyzer = analyzer
        self._required_sample_count = required_sample_count
        self._augmented_sample_count = augmented_sample_count
        self._pose_quotas = pose_quotas
        self._thresholds = thresholds
        self._clock = clock

    def create(self, command: CreateEnrollmentCommand) -> FaceEnrollment:
        if not command.consent_confirmed:
            raise ConsentRequiredError()
        active = self._repository.get_active()
        if active is not None:
            raise EnrollmentConflictError()
        now = self._clock()
        progress = tuple(
            PoseProgress(pose=pose, accepted_count=0, required_count=quota)
            for pose, quota in self._pose_quotas.items()
        )
        enrollment = FaceEnrollment(
            id=str(uuid4()),
            student_id=command.student_id,
            status=EnrollmentStatus.CREATED,
            consent_confirmed_by=command.consent_confirmed_by,
            consent_confirmed_at=now,
            valid_sample_count=0,
            required_sample_count=self._required_sample_count,
            pose_progress=progress,
            guidance_code="FIND_FACE",
            guidance_message=GUIDANCE_MESSAGES["FIND_FACE"],
            last_rejection_code=None,
            created_at=now,
            updated_at=now,
            completed_at=None,
        )
        created = self._repository.create(enrollment)
        self._object_storage.prepare_enrollment(
            enrollment.id, enrollment.student_id, enrollment.created_at
        )
        return created

    def get(self, enrollment_id: str) -> FaceEnrollment:
        enrollment = self._repository.get(enrollment_id)
        if enrollment is None:
            raise EnrollmentNotFoundError()
        return enrollment

    def process_frame(self, enrollment_id: str, frame: bytes) -> FrameDecision:
        if not frame:
            raise EnrollmentFrameError("빈 프레임은 처리할 수 없습니다.")
        enrollment = self.get(enrollment_id)
        if enrollment.status in {EnrollmentStatus.COMPLETE, EnrollmentStatus.ABORTED}:
            raise EnrollmentConflictError("종료된 얼굴 등록 세션입니다.")
        analysis = self._analyzer.analyze(enrollment_id, frame)
        rejected = rejection_code(analysis, self._thresholds)
        now = self._clock()
        if rejected is not None:
            status = (
                EnrollmentStatus.FACE_SEARCH
                if rejected in {"FACE_NOT_FOUND", "LOW_DETECTION_CONFIDENCE"}
                else EnrollmentStatus.GUIDING
            )
            updated = replace(
                enrollment,
                status=status,
                guidance_code=rejected,
                guidance_message=GUIDANCE_MESSAGES[rejected],
                last_rejection_code=rejected,
                updated_at=now,
            )
            return FrameDecision(self._repository.replace(updated), False, rejected)

        pose = classify_pose(analysis, self._thresholds)
        if pose_quota_complete(enrollment.pose_progress, pose) and not quotas_complete(
            enrollment.pose_progress
        ):
            guidance_code, guidance_message = _next_pose_guidance(enrollment.pose_progress)
            updated = replace(
                enrollment,
                status=EnrollmentStatus.GUIDING,
                guidance_code=guidance_code,
                guidance_message=f"이 방향은 충분합니다. {guidance_message}",
                last_rejection_code="POSE_QUOTA_FILLED",
                updated_at=now,
            )
            return FrameDecision(
                self._repository.replace(updated),
                False,
                "POSE_QUOTA_FILLED",
            )
        progress = increment_pose(enrollment.pose_progress, pose)
        sample_count = enrollment.valid_sample_count + 1
        sample_id = (
            f"{_safe_filename_component(enrollment.student_id)}_"
            f"{pose.value.lower()}_{sample_count:06d}"
        )
        self._object_storage.put_sample(
            enrollment.id,
            FaceSampleMetadata(
                sample_id=sample_id,
                pose=pose,
                captured_at=now,
                analysis=analysis,
            ),
            frame,
        )
        ready = sample_count >= enrollment.required_sample_count and quotas_complete(progress)
        if ready:
            validating = replace(
                enrollment,
                status=EnrollmentStatus.FINAL_VALIDATION,
                valid_sample_count=sample_count,
                pose_progress=progress,
                guidance_code="FINAL_VALIDATION",
                guidance_message=GUIDANCE_MESSAGES["FINAL_VALIDATION"],
                last_rejection_code=None,
                updated_at=now,
            )
            self._object_storage.finalize_dataset(
                enrollment.id,
                enrollment.student_id,
                self._augmented_sample_count,
            )
            self._repository.replace(validating)
            model_version = self._analyzer.finalize(enrollment.id)
            completed = replace(
                validating,
                status=EnrollmentStatus.COMPLETE,
                guidance_code="COMPLETE",
                guidance_message=GUIDANCE_MESSAGES["COMPLETE"],
                completed_at=now,
            )
            self._repository.replace(completed)
            self._repository.save_profile(
                FaceProfile(
                    student_id=enrollment.student_id,
                    enrollment_id=enrollment.id,
                    sample_count=sample_count,
                    model_version=model_version,
                    registered_at=now,
                )
            )
            return FrameDecision(completed, True, None)
        guidance_code, guidance_message = _next_pose_guidance(progress)
        updated = replace(
            enrollment,
            status=EnrollmentStatus.CAPTURING,
            valid_sample_count=sample_count,
            pose_progress=progress,
            guidance_code=guidance_code,
            guidance_message=guidance_message,
            last_rejection_code=None,
            updated_at=now,
        )
        return FrameDecision(self._repository.replace(updated), True, None)

    def abort(self, enrollment_id: str) -> None:
        enrollment = self.get(enrollment_id)
        if enrollment.status == EnrollmentStatus.COMPLETE:
            raise EnrollmentConflictError("완료된 얼굴 등록 세션은 취소할 수 없습니다.")
        self._object_storage.delete_enrollment(enrollment_id)
        try:
            self._analyzer.discard(enrollment_id)
        finally:
            self._repository.delete(enrollment_id)

    def get_profile(self, student_id: str) -> FaceProfile | None:
        return self._repository.get_profile(student_id)

    def delete_profile(self, student_id: str) -> None:
        profile = self._repository.get_profile(student_id)
        if profile is None:
            return
        self._object_storage.delete_enrollment(profile.enrollment_id)
        self._repository.delete_profile(student_id)
