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

    def create_for_student(self, student_id: str, enrollment_id: str) -> Student:
        student = self._students.get_student(student_id)
        images = self._dataset_reader.read_originals(
            enrollment_id=enrollment_id,
            student_id=student.id,
            student_number=student.student_number,
        )
        samples: list[SampleEmbedding] = []
        for image in _select_evenly(images, MAX_SOURCE_SAMPLES):
            try:
                sample = self._analyzer.create(image)
                _validate_sample(sample)
                samples.append(sample)
            except FaceEmbeddingInputError:
                continue
        if len(samples) < MIN_VALID_SAMPLES:
            raise FaceEmbeddingInputError(
                f"유효한 얼굴 이미지가 최소 {MIN_VALID_SAMPLES}장 필요합니다."
            )
        representative, used_count = _representative(samples)
        first = samples[0]
        now = self._clock()
        saved = self._repository.save(
            FaceEmbedding(
                id=self._id_factory(),
                student_id=student.id,
                student_name=student.name,
                student_number=student.student_number,
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
        )
        if saved.student_id != student.id:
            raise FaceEmbeddingInputError("저장된 얼굴 벡터의 학생 연결이 올바르지 않습니다.")
        return self._students.register_face(
            RegisterStudentFaceCommand(student_id=student.id, enrollment_id=enrollment_id)
        )

    def find_by_student(self, student_id: str) -> FaceEmbedding | None:
        return self._repository.find_by_student(student_id)

    def delete_for_student(self, student_id: str) -> None:
        self._repository.delete_by_student(student_id)


def _select_evenly(values: list[bytes], maximum: int) -> list[bytes]:
    if len(values) <= maximum:
        return values
    return [values[round(index * (len(values) - 1) / (maximum - 1))] for index in range(maximum)]


def _validate_sample(sample: SampleEmbedding) -> None:
    if sample.dimension != 512 or len(sample.vector) != 512:
        raise FaceEmbeddingInputError("얼굴 embedding 차원은 512여야 합니다.")
    if not sample.normalized or any(not math.isfinite(value) for value in sample.vector):
        raise FaceEmbeddingInputError("정규화된 유한 embedding 값이 필요합니다.")
    norm = math.sqrt(sum(value * value for value in sample.vector))
    if not math.isclose(norm, 1.0, rel_tol=1e-3, abs_tol=1e-3):
        raise FaceEmbeddingInputError("얼굴 embedding의 L2 norm이 올바르지 않습니다.")


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
