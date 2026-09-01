"""얼굴 등록 세션과 완료 프로필 MongoDB 저장소.

**컬렉션을 둘로 나누고 수명을 다르게 둔다.** 결정 0011이 미완료 세션과 완료 원본의
수명을 구분했기 때문이다.

- `face_enrollments` — 진행 중 세션. TTL 인덱스로 자동 만료시킨다. memory 저장소는
  프로세스가 죽으면 세션이 함께 사라졌지만 MongoDB는 남는다. 끊긴 세션이 영구히
  쌓이면 미완료 데이터를 폐기한다는 결정 0011 4항이 깨지므로 만료를 DB에 맡긴다.
- `face_profiles` — 완료된 등록. 재학 기간 또는 동의 철회까지 보관한다(5항).

embedding과 원본 이미지는 여기 없다. 각각 face_embeddings 컬렉션과 객체 저장소가
갖는다. 결정 0011 5항이 원본·객체 키·embedding을 API와 로그에 노출하지 말라고 정했다.
"""

from __future__ import annotations

from datetime import datetime

from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import PyMongoError

from ...shared.database import MongoDatabase, MongoDocument
from ...shared.errors import RepositoryDataError, RepositoryUnavailableError
from ..models import EnrollmentStatus, FaceEnrollment, FaceProfile, PoseBin, PoseProgress

# 진행 중 세션의 보존 상한. 결정 0011은 연결이 끊기면 폐기한다고 정했고 정상 경로에서는
# abort/complete가 즉시 지운다. 이 값은 그 경로를 타지 못하고 남은 세션의 안전망이다.
# 등록 한 번이 수 분이면 끝나므로 1시간이면 정상 세션을 건드리지 않는다.
ENROLLMENT_TTL_SECONDS = 3600

_ACTIVE_STATUSES = tuple(
    status.value
    for status in EnrollmentStatus
    if status not in {EnrollmentStatus.COMPLETE, EnrollmentStatus.ABORTED}
)


class MongoFaceEnrollmentRepository:
    """FaceEnrollmentRepository 포트의 MongoDB 구현."""

    enrollment_collection_name = "face_enrollments"
    profile_collection_name = "face_profiles"

    def __init__(self, database: MongoDatabase) -> None:
        self._enrollments = database[self.enrollment_collection_name]
        self._profiles = database[self.profile_collection_name]

    @staticmethod
    def ensure_indexes(database: MongoDatabase) -> None:
        enrollments = database[MongoFaceEnrollmentRepository.enrollment_collection_name]
        # updated_at 기준으로 만료시킨다. created_at을 쓰면 오래 진행 중인 세션도
        # 지워질 수 있다 — 갱신이 멈춘 세션만 대상이 되게 한다.
        enrollments.create_index(
            [("updated_at", ASCENDING)],
            name="ttl_face_enrollments_updated_at",
            expireAfterSeconds=ENROLLMENT_TTL_SECONDS,
        )
        # get_active가 매번 전체를 스캔하지 않게 한다. 활성 세션은 한 번에 하나뿐이지만
        # 만료 전 종료 세션이 함께 쌓여 있을 수 있다.
        enrollments.create_index(
            [("status", ASCENDING)],
            name="ix_face_enrollments_status",
        )
        profiles = database[MongoFaceEnrollmentRepository.profile_collection_name]
        profiles.create_index(
            [("student_id", ASCENDING)],
            name="uq_face_profiles_student_id",
            unique=True,
        )

    def create(self, enrollment: FaceEnrollment) -> FaceEnrollment:
        try:
            self._enrollments.insert_one(_enrollment_to_document(enrollment))
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return enrollment

    def get(self, enrollment_id: str) -> FaceEnrollment | None:
        try:
            document = self._enrollments.find_one({"_id": enrollment_id})
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else _enrollment_to_domain(document)

    def get_active(self) -> FaceEnrollment | None:
        try:
            document = self._enrollments.find_one({"status": {"$in": list(_ACTIVE_STATUSES)}})
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else _enrollment_to_domain(document)

    def replace(self, enrollment: FaceEnrollment) -> FaceEnrollment:
        values = _enrollment_to_document(enrollment)
        values.pop("_id")
        try:
            document = self._enrollments.find_one_and_update(
                {"_id": enrollment.id},
                {"$set": values},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        if document is None:
            raise RepositoryUnavailableError()
        return _enrollment_to_domain(document)

    def delete(self, enrollment_id: str) -> None:
        try:
            self._enrollments.delete_one({"_id": enrollment_id})
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def get_profile(self, student_id: str) -> FaceProfile | None:
        try:
            document = self._profiles.find_one({"student_id": student_id})
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else _profile_to_domain(document)

    def save_profile(self, profile: FaceProfile) -> FaceProfile:
        values = _profile_to_document(profile)
        values.pop("_id")
        try:
            document = self._profiles.find_one_and_update(
                {"student_id": profile.student_id},
                {"$set": values},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        if document is None:
            raise RepositoryUnavailableError()
        return _profile_to_domain(document)

    def delete_profile(self, student_id: str) -> None:
        try:
            self._profiles.delete_one({"student_id": student_id})
        except PyMongoError:
            raise RepositoryUnavailableError() from None


def _enrollment_to_document(value: FaceEnrollment) -> MongoDocument:
    return {
        "_id": value.id,
        "student_id": value.student_id,
        "status": value.status.value,
        "consent_confirmed_by": value.consent_confirmed_by,
        "consent_confirmed_at": value.consent_confirmed_at,
        "valid_sample_count": value.valid_sample_count,
        "required_sample_count": value.required_sample_count,
        "pose_progress": [
            {
                "pose": item.pose.value,
                "accepted_count": item.accepted_count,
                "required_count": item.required_count,
            }
            for item in value.pose_progress
        ],
        "guidance_code": value.guidance_code,
        "guidance_message": value.guidance_message,
        "last_rejection_code": value.last_rejection_code,
        "created_at": value.created_at,
        "updated_at": value.updated_at,
        "completed_at": value.completed_at,
    }


def _enrollment_to_domain(document: MongoDocument) -> FaceEnrollment:
    try:
        progress_value = document["pose_progress"]
        if not isinstance(progress_value, list):
            raise TypeError
        pose_progress = tuple(
            PoseProgress(
                pose=PoseBin(item["pose"]),
                accepted_count=int(item["accepted_count"]),
                required_count=int(item["required_count"]),
            )
            for item in progress_value
        )
        completed_at = document["completed_at"]
        if completed_at is not None and not isinstance(completed_at, datetime):
            raise TypeError
        return FaceEnrollment(
            id=str(document["_id"]),
            student_id=str(document["student_id"]),
            status=EnrollmentStatus(document["status"]),
            consent_confirmed_by=str(document["consent_confirmed_by"]),
            consent_confirmed_at=_require_datetime(document["consent_confirmed_at"]),
            valid_sample_count=int(document["valid_sample_count"]),
            required_sample_count=int(document["required_sample_count"]),
            pose_progress=pose_progress,
            guidance_code=str(document["guidance_code"]),
            guidance_message=str(document["guidance_message"]),
            last_rejection_code=(
                None
                if document["last_rejection_code"] is None
                else str(document["last_rejection_code"])
            ),
            created_at=_require_datetime(document["created_at"]),
            updated_at=_require_datetime(document["updated_at"]),
            completed_at=completed_at,
        )
    except (KeyError, TypeError, ValueError):
        raise RepositoryDataError() from None


def _profile_to_document(value: FaceProfile) -> MongoDocument:
    return {
        "_id": value.student_id,
        "student_id": value.student_id,
        "enrollment_id": value.enrollment_id,
        "sample_count": value.sample_count,
        "model_version": value.model_version,
        "registered_at": value.registered_at,
    }


def _profile_to_domain(document: MongoDocument) -> FaceProfile:
    try:
        return FaceProfile(
            student_id=str(document["student_id"]),
            enrollment_id=str(document["enrollment_id"]),
            sample_count=int(document["sample_count"]),
            model_version=str(document["model_version"]),
            registered_at=_require_datetime(document["registered_at"]),
        )
    except (KeyError, TypeError, ValueError):
        raise RepositoryDataError() from None


def _require_datetime(value: object) -> datetime:
    """MongoDB에서 읽은 값이 timezone-aware datetime인지 확인한다.

    naive datetime을 그대로 도메인에 들이면 비교와 직렬화에서 조용히 어긋난다.
    """
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise TypeError
    return value
