"""얼굴 embedding 서비스 단위 테스트용 저장소."""

from dataclasses import replace

from ..models import FaceEmbedding


class InMemoryFaceEmbeddingRepository:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], FaceEmbedding] = {}

    def save(self, embedding: FaceEmbedding) -> FaceEmbedding:
        key = (embedding.model_name, embedding.student_id)
        existing = self._items.get(key)
        if existing is not None:
            embedding = replace(embedding, id=existing.id, created_at=existing.created_at)
        self._items[key] = embedding
        return embedding

    def find_by_student(self, student_id: str, model_name: str) -> FaceEmbedding | None:
        return self._items.get((model_name, student_id))

    def list_student_ids(self, model_name: str) -> set[str]:
        return {
            student_id
            for saved_model_name, student_id in self._items
            if saved_model_name == model_name
        }

    def delete_by_student(self, student_id: str) -> None:
        for key in tuple(self._items):
            if key[1] == student_id:
                self._items.pop(key)
