"""얼굴 등록 API 스키마."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .models import FaceEnrollment, FaceProfile, FrameDecision, PoseProgress


class CreateEnrollmentRequest(BaseModel):
    consent_confirmed: bool
    consent_confirmed_by: str = Field(min_length=1, max_length=100)


class PoseProgressResponse(BaseModel):
    pose: str
    accepted_count: int
    required_count: int
    completion_percent: int

    @classmethod
    def from_domain(cls, item: PoseProgress) -> PoseProgressResponse:
        percent = min(100, round(item.accepted_count / item.required_count * 100))
        return cls(
            pose=item.pose.value,
            accepted_count=item.accepted_count,
            required_count=item.required_count,
            completion_percent=percent,
        )


class EnrollmentResponse(BaseModel):
    id: str
    student_id: str
    status: str
    valid_sample_count: int
    required_sample_count: int
    completion_percent: int
    pose_progress: list[PoseProgressResponse]
    guidance_code: str
    guidance_message: str
    last_rejection_code: str | None
    consent_confirmed_by: str
    consent_confirmed_at: datetime
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    @classmethod
    def from_domain(cls, item: FaceEnrollment) -> EnrollmentResponse:
        return cls(
            id=item.id,
            student_id=item.student_id,
            status=item.status.value,
            valid_sample_count=item.valid_sample_count,
            required_sample_count=item.required_sample_count,
            completion_percent=min(
                100, round(item.valid_sample_count / item.required_sample_count * 100)
            ),
            pose_progress=[PoseProgressResponse.from_domain(x) for x in item.pose_progress],
            guidance_code=item.guidance_code,
            guidance_message=item.guidance_message,
            last_rejection_code=item.last_rejection_code,
            consent_confirmed_by=item.consent_confirmed_by,
            consent_confirmed_at=item.consent_confirmed_at,
            created_at=item.created_at,
            updated_at=item.updated_at,
            completed_at=item.completed_at,
        )


class FrameDecisionResponse(BaseModel):
    accepted: bool
    rejection_code: str | None
    enrollment: EnrollmentResponse

    @classmethod
    def from_domain(cls, item: FrameDecision) -> FrameDecisionResponse:
        return cls(
            accepted=item.accepted,
            rejection_code=item.rejection_code,
            enrollment=EnrollmentResponse.from_domain(item.enrollment),
        )


class FaceProfileResponse(BaseModel):
    registered: bool
    student_id: str
    sample_count: int | None
    model_version: str | None
    registered_at: datetime | None

    @classmethod
    def from_domain(cls, student_id: str, item: FaceProfile | None) -> FaceProfileResponse:
        if item is None:
            return cls(
                registered=False,
                student_id=student_id,
                sample_count=None,
                model_version=None,
                registered_at=None,
            )
        return cls(
            registered=True,
            student_id=student_id,
            sample_count=item.sample_count,
            model_version=item.model_version,
            registered_at=item.registered_at,
        )
