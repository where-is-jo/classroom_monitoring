"""학생 대표 얼굴 embedding 생성 서비스 테스트."""

from datetime import UTC, date, datetime

import pytest

from app.face_embeddings.adapters.memory import InMemoryFaceEmbeddingRepository
from app.face_embeddings.errors import FaceEmbeddingInputError
from app.face_embeddings.models import SampleEmbedding
from app.face_embeddings.service import FaceEmbeddingService
from app.students.adapters.memory import InMemoryStudentRepository
from app.students.models import Student
from app.students.service import StudentService

NOW = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)


class Dataset:
    def __init__(self, count: int = 6) -> None:
        self.count = count

    def read_originals(
        self, *, enrollment_id: str, student_id: str, student_number: str
    ) -> list[bytes]:
        return [f"image-{index}".encode() for index in range(self.count)]


class Analyzer:
    def create(self, image: bytes) -> SampleEmbedding:
        vector = (1.0,) + (0.0,) * 511
        return SampleEmbedding(
            vector=vector,
            dimension=512,
            normalized=True,
            model_name="arcface",
            model_version="test-model",
            preprocessing_version="test-preprocessing",
        )


def make_service(count: int = 6) -> tuple[FaceEmbeddingService, StudentService]:
    student = Student(
        id="student-uuid",
        student_number="ST-001",
        name="테스트 학생",
        birth_date=date(2012, 1, 1),
        classroom_name="테스트반",
        phone=None,
        guardian_phone="010-0000-0000",
        face_enrollment_id=None,
        face_registered=False,
        is_active=True,
        created_at=NOW,
        updated_at=NOW,
    )
    students = StudentService(InMemoryStudentRepository((student,)), clock=lambda: NOW)
    service = FaceEmbeddingService(
        InMemoryFaceEmbeddingRepository(),
        Analyzer(),
        Dataset(count),
        students,
        clock=lambda: NOW,
        id_factory=lambda: "embedding-uuid",
    )
    return service, students


def test_embedding_is_saved_before_student_becomes_complete() -> None:
    service, students = make_service()

    updated = service.create_for_student("student-uuid", "enrollment-uuid")
    embedding = service.find_by_student("student-uuid", "arcface")

    assert embedding is not None
    assert embedding.id == "embedding-uuid"
    assert embedding.dimension == 512
    assert embedding.source_sample_count == 6
    assert sum(value * value for value in embedding.vector) == pytest.approx(1.0)
    assert updated.face_registered is True
    assert students.get_student("student-uuid").face_enrollment_id == "enrollment-uuid"


def test_registered_student_ids_are_separated_by_model() -> None:
    service, _ = make_service()

    service.create_for_student("student-uuid", "enrollment-uuid")

    assert service.registered_student_ids("arcface") == {"student-uuid"}
    assert service.registered_student_ids("adaface") == set()


def test_active_model_mismatch_does_not_save_or_complete_student() -> None:
    service, students = make_service()

    with pytest.raises(FaceEmbeddingInputError, match="활성 모델"):
        service.create_for_student(
            "student-uuid",
            "enrollment-uuid",
            expected_model_name="adaface",
        )

    assert service.find_by_student("student-uuid", "arcface") is None
    assert students.get_student("student-uuid").face_registered is False


def test_too_few_valid_samples_does_not_complete_student() -> None:
    service, students = make_service(count=4)

    with pytest.raises(FaceEmbeddingInputError):
        service.create_for_student("student-uuid", "enrollment-uuid")

    assert service.find_by_student("student-uuid", "arcface") is None
    assert students.get_student("student-uuid").face_registered is False


def test_deleting_face_embedding_removes_student_from_gallery_source() -> None:
    service, _ = make_service()
    service.create_for_student("student-uuid", "enrollment-uuid")

    service.delete_for_student("student-uuid")

    assert service.find_by_student("student-uuid", "arcface") is None


def test_deleting_face_embedding_also_clears_the_registration_flag() -> None:
    """동의 철회는 등록 표시까지 되돌려야 한다.

    벡터만 지우고 `face_registered`를 참으로 남기면, 갤러리 완전성 검사가 "등록됐다는데
    벡터가 없는 학생"을 보고 **전체 얼굴 식별을 닫는다.** 한 사람의 철회가 서비스 전체
    정지로 번지고, 사람이 학생 문서를 손으로 고치기 전까지 복구되지 않는다.
    """
    service, students = make_service()
    service.create_for_student("student-uuid", "enrollment-uuid")
    assert students.get_student("student-uuid").face_registered is True

    service.delete_for_student("student-uuid")

    student = students.get_student("student-uuid")
    assert student.face_registered is False
    assert student.face_enrollment_id is None


def test_서로_다른_모델_metadata가_섞이면_저장하지_않는다() -> None:
    class MixedAnalyzer(Analyzer):
        def create(self, image: bytes) -> SampleEmbedding:
            sample = super().create(image)
            if image == b"image-5":
                return SampleEmbedding(
                    vector=sample.vector,
                    dimension=sample.dimension,
                    normalized=sample.normalized,
                    model_name="adaface",
                    model_version="adaface-test",
                    preprocessing_version="adaface-test-preprocessing",
                )
            return sample

    service, students = make_service()
    service._analyzer = MixedAnalyzer()

    with pytest.raises(FaceEmbeddingInputError, match="metadata"):
        service.create_for_student("student-uuid", "enrollment-uuid")

    assert service.find_by_student("student-uuid", "arcface") is None
    assert students.get_student("student-uuid").face_registered is False
