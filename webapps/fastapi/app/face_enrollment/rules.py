"""프레임 품질과 pose 진행률의 순수 규칙."""

from __future__ import annotations

from dataclasses import dataclass

from .models import FaceAnalysis, PoseBin, PoseProgress


@dataclass(frozen=True)
class EnrollmentThresholds:
    detection_confidence_min: float
    face_size_ratio_min: float
    roll_degrees_max: float
    blur_score_min: float
    brightness_score_min: float
    landmark_confidence_min: float
    occlusion_score_max: float
    duplicate_score_max: float
    motion_speed_dps_max: float
    yaw_side_degrees: float
    pitch_side_degrees: float


def classify_pose(analysis: FaceAnalysis, thresholds: EnrollmentThresholds) -> PoseBin:
    yaw_ratio = abs(analysis.yaw_degrees) / thresholds.yaw_side_degrees
    pitch_ratio = abs(analysis.pitch_degrees) / thresholds.pitch_side_degrees
    if max(yaw_ratio, pitch_ratio) < 1:
        return PoseBin.FRONT
    if yaw_ratio >= pitch_ratio:
        return PoseBin.RIGHT if analysis.yaw_degrees < 0 else PoseBin.LEFT
    return PoseBin.UP if analysis.pitch_degrees < 0 else PoseBin.DOWN


def rejection_code(analysis: FaceAnalysis, thresholds: EnrollmentThresholds) -> str | None:
    if analysis.face_count == 0:
        return "FACE_NOT_FOUND"
    if analysis.face_count > 1:
        return "MULTIPLE_FACES"
    if analysis.detection_confidence < thresholds.detection_confidence_min:
        return "LOW_DETECTION_CONFIDENCE"
    if analysis.face_size_ratio < thresholds.face_size_ratio_min:
        return "FACE_TOO_SMALL"
    if not analysis.centered:
        return "FACE_NOT_CENTERED"
    pose = classify_pose(analysis, thresholds)
    if pose == PoseBin.FRONT and abs(analysis.roll_degrees) > thresholds.roll_degrees_max:
        return "HEAD_TILTED"
    if analysis.blur_score < thresholds.blur_score_min:
        return "BLURRY"
    if analysis.brightness_score < thresholds.brightness_score_min:
        return "BAD_LIGHTING"
    if analysis.landmark_confidence < thresholds.landmark_confidence_min:
        return "LANDMARK_UNSTABLE"
    if analysis.motion_speed_dps > thresholds.motion_speed_dps_max:
        return "MOVING_TOO_FAST"
    if analysis.occlusion_score > thresholds.occlusion_score_max:
        return "OCCLUDED"
    if analysis.duplicate_score > thresholds.duplicate_score_max:
        return "DUPLICATE"
    return None


def increment_pose(progress: tuple[PoseProgress, ...], pose: PoseBin) -> tuple[PoseProgress, ...]:
    return tuple(
        PoseProgress(
            pose=item.pose,
            accepted_count=item.accepted_count + (1 if item.pose == pose else 0),
            required_count=item.required_count,
        )
        for item in progress
    )


def pose_quota_complete(progress: tuple[PoseProgress, ...], pose: PoseBin) -> bool:
    item = next(item for item in progress if item.pose == pose)
    return item.accepted_count >= item.required_count


def quotas_complete(progress: tuple[PoseProgress, ...]) -> bool:
    return all(item.accepted_count >= item.required_count for item in progress)
