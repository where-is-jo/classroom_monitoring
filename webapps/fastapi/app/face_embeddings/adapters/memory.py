"""얼굴 embedding 서비스 단위 테스트용 저장소."""

from dataclasses import replace

from ..models import FaceEmbedding


class InMemoryFaceEmbeddingRepository:
    def __init__(self) -> None:
        self._items: dict[str, FaceEmbedding] = {}

    def save(self, embedding: FaceEmbedding) -> FaceEmbedding:
        existing = self._items.get(embedding.student_id)
        if existing is not None:
            embedding = replace(embedding, id=existing.id, created_at=existing.created_at)
        self._items[embedding.student_id] = embedding
        return embedding

    def find_by_student(self, student_id: str) -> FaceEmbedding | None:
        return self._items.get(student_id)

    def delete_by_student(self, student_id: str) -> None:
        self._items.pop(student_id, None)
