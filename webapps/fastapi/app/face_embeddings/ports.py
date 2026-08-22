"""얼굴 embedding 처리의 프로세스 외부 경계."""

from typing import Protocol

from .models import FaceEmbedding, SampleEmbedding


class FaceEmbeddingRepository(Protocol):
    def save(self, embedding: FaceEmbedding) -> FaceEmbedding: ...
    def find_by_student(self, student_id: str) -> FaceEmbedding | None: ...
    def delete_by_student(self, student_id: str) -> None: ...


class FaceEmbeddingAnalyzer(Protocol):
    def create(self, image: bytes) -> SampleEmbedding: ...


class FaceDatasetReader(Protocol):
    def read_originals(
        self, *, enrollment_id: str, student_id: str, student_number: str
    ) -> list[bytes]: ...
