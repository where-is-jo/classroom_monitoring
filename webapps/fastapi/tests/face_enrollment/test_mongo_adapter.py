"""얼굴 등록 MongoDB 저장소의 index 계약과 직렬화.

결정 0011은 미완료 세션과 완료 프로필의 수명을 다르게 정했다. memory 저장소에서는
프로세스가 죽으면 세션이 함께 사라졌지만 MongoDB는 남으므로, 그 수명 차이가
컬렉션 분리와 TTL index로 실제로 표현되는지를 고정한다.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.face_enrollment.adapters.mongo import (
    ENROLLMENT_TTL_SECONDS,
    MongoFaceEnrollmentRepository,
)
from app.face_enrollment.models import (
    EnrollmentStatus,
    FaceEnrollment,
    FaceProfile,
    PoseBin,
    PoseProgress,
)
from app.shared.errors import RepositoryDataError

NOW = datetime(2026, 8, 18, 3, 0, tzinfo=UTC)


class RecordingCollection:
    def __init__(self) -> None:
        self.indexes: list[tuple[list[tuple[str, int]], dict[str, object]]] = []
        self.inserted: list[dict[str, object]] = []
        self.queries: list[dict[str, object]] = []
        self.deletes: list[dict[str, object]] = []
        self.returned_document: dict[str, object] | None = None

    def create_index(self, fields: list[tuple[str, int]], **options: object) -> None:
        self.indexes.append((fields, options))

    def insert_one(self, document: dict[str, object]) -> None:
        self.inserted.append(document)

    def find_one(self, query: dict[str, object]) -> dict[str, object] | None:
        self.queries.append(query)
        return self.returned_document

    def find_one_and_update(
        self,
        query: dict[str, object],
        update: dict[str, object],
        *,
        upsert: bool = False,
        return_document: object = None,
    ) -> dict[str, object] | None:
        del upsert, return_document
        self.queries.append(query)
        return self.returned_document

    def delete_one(self, query: dict[str, object]) -> None:
        self.deletes.append(query)


class RecordingDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, RecordingCollection] = {}

    def __getitem__(self, name: str) -> RecordingCollection:
        return self.collections.setdefault(name, RecordingCollection())


def _enrollment() -> FaceEnrollment:
    return FaceEnrollment(
        id="enrollment-01",
        student_id="student-01",
        status=EnrollmentStatus.CAPTURING,
        consent_confirmed_by="admin-01",
        consent_confirmed_at=NOW,
        valid_sample_count=12,
        required_sample_count=120,
        pose_progress=(PoseProgress(pose=PoseBin.FRONT, accepted_count=12, required_count=32),),
        guidance_code="POSE_FRONT",
        guidance_message="정면을 봐 주세요",
        last_rejection_code=None,
        created_at=NOW,
        updated_at=NOW,
        completed_at=None,
    )


def _profile() -> FaceProfile:
    return FaceProfile(
        student_id="student-01",
        enrollment_id="enrollment-01",
        sample_count=300,
        model_version="v1",
        registered_at=NOW,
    )


def test_세션과_프로필을_다른_컬렉션에_담는다() -> None:
    database = RecordingDatabase()
    repository = MongoFaceEnrollmentRepository(database)  # type: ignore[arg-type]

    repository.create(_enrollment())
    # save_profile은 upsert 후 갱신된 문서를 되읽는다. 대역이 돌려줄 값을 미리 둔다.
    database["face_profiles"].returned_document = _profile_document()
    repository.save_profile(_profile())

    assert "face_enrollments" in database.collections
    assert "face_profiles" in database.collections
    assert len(database.collections["face_enrollments"].inserted) == 1


def test_진행_중_세션에만_TTL_index를_건다() -> None:
    """미완료 세션은 폐기한다는 결정 0011 4항을 DB가 보장하게 한다."""
    database = RecordingDatabase()

    MongoFaceEnrollmentRepository.ensure_indexes(database)  # type: ignore[arg-type]

    enrollment_options = [
        options for _fields, options in database.collections["face_enrollments"].indexes
    ]
    ttl_values = [
        options["expireAfterSeconds"]
        for options in enrollment_options
        if "expireAfterSeconds" in options
    ]
    assert ttl_values == [ENROLLMENT_TTL_SECONDS]

    # 완료 프로필은 재학 기간까지 보관한다(5항). 만료가 걸리면 안 된다.
    profile_options = [
        options for _fields, options in database.collections["face_profiles"].indexes
    ]
    assert all("expireAfterSeconds" not in options for options in profile_options)


def test_TTL은_created_at이_아니라_updated_at을_본다() -> None:
    """오래 진행 중인 세션이 만료되지 않게 한다."""
    database = RecordingDatabase()

    MongoFaceEnrollmentRepository.ensure_indexes(database)  # type: ignore[arg-type]

    ttl_fields = [
        fields
        for fields, options in database.collections["face_enrollments"].indexes
        if "expireAfterSeconds" in options
    ]
    assert ttl_fields == [[("updated_at", 1)]]


def test_학생당_프로필은_하나다() -> None:
    database = RecordingDatabase()

    MongoFaceEnrollmentRepository.ensure_indexes(database)  # type: ignore[arg-type]

    unique_fields = [
        fields
        for fields, options in database.collections["face_profiles"].indexes
        if options.get("unique") is True
    ]
    assert unique_fields == [[("student_id", 1)]]


def test_활성_세션_조회는_종료_상태를_제외한다() -> None:
    database = RecordingDatabase()
    repository = MongoFaceEnrollmentRepository(database)  # type: ignore[arg-type]

    repository.get_active()

    query = database.collections["face_enrollments"].queries[0]
    statuses = query["status"]["$in"]  # type: ignore[index]
    assert "COMPLETE" not in statuses
    assert "ABORTED" not in statuses
    assert "CAPTURING" in statuses


def test_pose_progress를_왕복해도_값이_유지된다() -> None:
    database = RecordingDatabase()
    repository = MongoFaceEnrollmentRepository(database)  # type: ignore[arg-type]
    original = _enrollment()

    repository.create(original)
    stored = database.collections["face_enrollments"].inserted[0]
    database.collections["face_enrollments"].returned_document = stored

    restored = repository.get("enrollment-01")

    assert restored == original


def test_naive_datetime은_데이터_오류로_거절한다() -> None:
    """timezone 없는 값이 도메인에 들어오면 비교와 직렬화가 조용히 어긋난다."""
    database = RecordingDatabase()
    repository = MongoFaceEnrollmentRepository(database)  # type: ignore[arg-type]
    broken = dict(_profile_document())
    broken["registered_at"] = datetime(2026, 8, 18, 3, 0)  # noqa: DTZ001
    database.collections["face_profiles"].returned_document = broken

    with pytest.raises(RepositoryDataError):
        repository.get_profile("student-01")


def _profile_document() -> dict[str, object]:
    return {
        "_id": "student-01",
        "student_id": "student-01",
        "enrollment_id": "enrollment-01",
        "sample_count": 300,
        "model_version": "v1",
        "registered_at": NOW,
    }
