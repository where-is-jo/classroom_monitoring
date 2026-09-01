"""얼굴 원본에서 학생 대표 embedding을 생성하는 서비스."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime
from statistics import median
from uuid import uuid4

from ..students.models import RegisterStudentFaceCommand, Student
from ..students.service import StudentService
from .errors import FaceEmbeddingInputError
from .models import FaceEmbedding, SampleEmbedding
from .ports import FaceDatasetReader, FaceEmbeddingAnalyzer, FaceEmbeddingRepository

MIN_VALID_SAMPLES = 5
MAX_SOURCE_SAMPLES = 25


def _new_embedding_id() -> str:
    return str(uuid4())


class FaceEmbeddingService:
    def __init__(
        self,
        repository: FaceEmbeddingRepository,
        analyzer: FaceEmbeddingAnalyzer,
        dataset_reader: FaceDatasetReader,
        students: StudentService,
        *,
        clock: Callable[[], datetime],
        id_factory: Callable[[], str] = _new_embedding_id,
    ) -> None:
        self._repository = repository
        self._analyzer = analyzer
        self._dataset_reader = dataset_reader
        self._students = students
        self._clock = clock
        self._id_factory = id_factory

    def create_for_student(
        self,
        student_id: str,
        enrollment_id: str,
        *,
        expected_model_name: str | None = None,
    ) -> Student:
        student = self._students.get_student(student_id)
        images = self._dataset_reader.read_originals(
            enrollment_id=enrollment_id,
            student_id=student.id,
            student_number=student.student_number,
        )
        now = self._clock()
        embedding = build_face_embedding(
            student_id=student.id,
            student_name=student.name,
            student_number=student.student_number,
            enrollment_id=enrollment_id,
            images=images,
            analyzer=self._analyzer,
            now=now,
            embedding_id=self._id_factory(),
        )
        if expected_model_name is not None and embedding.model_name != expected_model_name:
            raise FaceEmbeddingInputError(
                "얼굴 분석 서버의 활성 모델이 관리자 화면 설정과 일치하지 않습니다."
            )
        saved = self._repository.save(embedding)
        if saved.student_id != student.id:
            raise FaceEmbeddingInputError("저장된 얼굴 벡터의 학생 연결이 올바르지 않습니다.")
        return self._students.register_face(
            RegisterStudentFaceCommand(student_id=student.id, enrollment_id=enrollment_id)
        )

    def find_by_student(self, student_id: str, model_name: str) -> FaceEmbedding | None:
        return self._repository.find_by_student(student_id, model_name)

    def registered_student_ids(self, model_name: str) -> set[str]:
        return self._repository.list_student_ids(model_name)

    def delete_for_student(self, student_id: str) -> None:
        """동의 철회. 벡터를 지우고 **등록 표시도 함께 되돌린다.**

        표시를 남겨 두면 갤러리 완전성 검사가 "등록됐다는데 벡터가 없는 학생"을 보고
        전체 얼굴 식별을 닫는다. 한 사람의 철회가 서비스 전체 정지로 번진다.
        """
        self._repository.delete_by_student(student_id)
        self._students.unregister_face(student_id)


def select_evenly[T](values: list[T], maximum: int) -> list[T]:
    if maximum < 1:
        raise ValueError("선택 상한은 1 이상이어야 합니다.")
    if len(values) <= maximum:
        return values
    if maximum == 1:
        return [values[len(values) // 2]]
    return [values[round(index * (len(values) - 1) / (maximum - 1))] for index in range(maximum)]


def build_face_embedding(
    *,
    student_id: str,
    student_name: str,
    student_number: str,
    enrollment_id: str,
    images: list[bytes],
    analyzer: FaceEmbeddingAnalyzer,
    now: datetime,
    embedding_id: str,
) -> FaceEmbedding:
    """이미지 집합을 검증해 저장 전 대표 embedding 도메인 객체로 만든다."""

    samples: list[SampleEmbedding] = []
    for image in select_evenly(images, MAX_SOURCE_SAMPLES):
        try:
            sample = analyzer.create(image)
            _validate_sample(sample)
            samples.append(sample)
        except FaceEmbeddingInputError:
            continue
    if len(samples) < MIN_VALID_SAMPLES:
        raise FaceEmbeddingInputError(
            f"유효한 얼굴 이미지가 최소 {MIN_VALID_SAMPLES}장 필요합니다."
        )
    _validate_consistent_metadata(samples)
    representative, used_count = _representative(samples)
    first = samples[0]
    return FaceEmbedding(
        id=embedding_id,
        student_id=student_id,
        student_name=student_name,
        student_number=student_number,
        enrollment_id=enrollment_id,
        vector=representative,
        dimension=len(representative),
        normalized=True,
        model_name=first.model_name,
        model_version=first.model_version,
        preprocessing_version=first.preprocessing_version,
        source_sample_count=used_count,
        created_at=now,
        updated_at=now,
    )


def _validate_sample(sample: SampleEmbedding) -> None:
    if sample.dimension != 512 or len(sample.vector) != 512:
        raise FaceEmbeddingInputError("얼굴 embedding 차원은 512여야 합니다.")
    if not sample.normalized or any(not math.isfinite(value) for value in sample.vector):
        raise FaceEmbeddingInputError("정규화된 유한 embedding 값이 필요합니다.")
    norm = math.sqrt(sum(value * value for value in sample.vector))
    if not math.isclose(norm, 1.0, rel_tol=1e-3, abs_tol=1e-3):
        raise FaceEmbeddingInputError("얼굴 embedding의 L2 norm이 올바르지 않습니다.")


def _validate_consistent_metadata(samples: list[SampleEmbedding]) -> None:
    expected = (
        samples[0].model_name,
        samples[0].model_version,
        samples[0].preprocessing_version,
    )
    if expected[0] not in {"arcface", "adaface"}:
        raise FaceEmbeddingInputError("지원하지 않는 얼굴 embedding 모델입니다.")
    if any(
        (
            sample.model_name,
            sample.model_version,
            sample.preprocessing_version,
        )
        != expected
        for sample in samples[1:]
    ):
        raise FaceEmbeddingInputError("얼굴 embedding 샘플의 모델 metadata가 서로 다릅니다.")


def _normalize(vector: list[float]) -> tuple[float, ...]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 1e-12 or not math.isfinite(norm):
        raise FaceEmbeddingInputError("대표 얼굴 embedding을 계산할 수 없습니다.")
    return tuple(value / norm for value in vector)


def _representative(samples: list[SampleEmbedding]) -> tuple[tuple[float, ...], int]:
    preliminary = _normalize(
        [sum(sample.vector[index] for sample in samples) for index in range(512)]
    )
    similarities = [
        sum(value * center for value, center in zip(sample.vector, preliminary, strict=True))
        for sample in samples
    ]
    cutoff = max(0.15, median(similarities) - 0.15)
    kept = [
        sample
        for sample, similarity in zip(samples, similarities, strict=True)
        if similarity >= cutoff
    ]
    if len(kept) < MIN_VALID_SAMPLES:
        raise FaceEmbeddingInputError("서로 일관된 얼굴 embedding 샘플이 부족합니다.")
    representative = _normalize(
        [sum(sample.vector[index] for sample in kept) for index in range(512)]
    )
    return representative, len(kept)
