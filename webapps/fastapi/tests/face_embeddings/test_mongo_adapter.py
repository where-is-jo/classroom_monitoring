"""모델별 얼굴 embedding 컬렉션 라우팅 계약."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.face_embeddings.adapters.mongo import MongoFaceEmbeddingRepository
from app.face_embeddings.models import FaceEmbedding
from app.shared.errors import RepositoryDataError

NOW = datetime(2026, 8, 25, 0, 0, tzinfo=UTC)


class RecordingCollection:
    def __init__(self) -> None:
        self.indexes: list[tuple[list[tuple[str, int]], dict[str, object]]] = []
        self.updates: list[tuple[dict[str, object], dict[str, object]]] = []
        self.queries: list[dict[str, object]] = []
        self.list_queries: list[tuple[dict[str, object], dict[str, object]]] = []
        self.deletes: list[dict[str, object]] = []
        self.returned_document: dict[str, object] | None = None
        self.returned_documents: list[dict[str, object]] = []

    def create_index(self, fields: list[tuple[str, int]], **options: object) -> None:
        self.indexes.append((fields, options))

    def find_one_and_update(
        self,
        query: dict[str, object],
        update: dict[str, object],
        **_options: object,
    ) -> dict[str, object] | None:
        self.updates.append((query, update))
        return self.returned_document

    def find_one(self, query: dict[str, object]) -> dict[str, object] | None:
        self.queries.append(query)
        return self.returned_document

    def find(
        self, query: dict[str, object], projection: dict[str, object]
    ) -> list[dict[str, object]]:
        self.list_queries.append((query, projection))
        return self.returned_documents

    def delete_one(self, query: dict[str, object]) -> None:
        self.deletes.append(query)


class RecordingDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, RecordingCollection] = {}

    def __getitem__(self, name: str) -> RecordingCollection:
        return self.collections.setdefault(name, RecordingCollection())


def embedding(model_name: str) -> FaceEmbedding:
    return FaceEmbedding(
        id=f"embedding-{model_name}",
        student_id="student-01",
        student_name="테스트 학생",
        student_number="ST-001",
        enrollment_id="enrollment-01",
        vector=(1.0,) + (0.0,) * 511,
        dimension=512,
        normalized=True,
        model_name=model_name,
        model_version=f"{model_name}-v1",
        preprocessing_version=f"{model_name}-crop-v1",
        source_sample_count=6,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.parametrize(
    ("model_name", "collection_name"),
    [
        ("arcface", "face_embeddings_arcface"),
        ("adaface", "face_embeddings_adaface"),
    ],
)
def test_저장은_모델별_컬렉션으로만_간다(model_name: str, collection_name: str) -> None:
    database = RecordingDatabase()
    repository = MongoFaceEmbeddingRepository(database)  # type: ignore[arg-type]
    value = embedding(model_name)
    database[collection_name].returned_document = {
        "_id": value.id,
        "student_id": value.student_id,
        "student_name": value.student_name,
        "student_number": value.student_number,
        "enrollment_id": value.enrollment_id,
        "vector": list(value.vector),
        "dimension": value.dimension,
        "normalized": value.normalized,
        "model_name": value.model_name,
        "model_version": value.model_version,
        "preprocessing_version": value.preprocessing_version,
        "source_sample_count": value.source_sample_count,
        "created_at": value.created_at,
        "updated_at": value.updated_at,
    }

    repository.save(value)

    assert len(database[collection_name].updates) == 1
    other = "face_embeddings_adaface" if model_name == "arcface" else "face_embeddings_arcface"
    assert database[other].updates == []
    assert database["face_embeddings"].updates == []


def test_조회는_모델명을_반드시_요구하고_해당_컬렉션만_본다() -> None:
    database = RecordingDatabase()
    repository = MongoFaceEmbeddingRepository(database)  # type: ignore[arg-type]

    repository.find_by_student("student-01", "adaface")

    assert database["face_embeddings_adaface"].queries == [{"student_id": "student-01"}]
    assert database["face_embeddings_arcface"].queries == []


def test_모델별_등록_학생_ID를_한_번에_조회한다() -> None:
    database = RecordingDatabase()
    repository = MongoFaceEmbeddingRepository(database)  # type: ignore[arg-type]
    database["face_embeddings_adaface"].returned_documents = [
        {"student_id": "student-01"},
        {"student_id": "student-02"},
    ]

    result = repository.list_student_ids("adaface")

    assert result == {"student-01", "student-02"}
    assert database["face_embeddings_adaface"].list_queries == [({}, {"_id": 0, "student_id": 1})]
    assert database["face_embeddings_arcface"].list_queries == []


def test_삭제는_두_모델과_레거시를_모두_정리한다() -> None:
    database = RecordingDatabase()
    repository = MongoFaceEmbeddingRepository(database)  # type: ignore[arg-type]

    repository.delete_by_student("student-01")

    for name in (
        "face_embeddings_arcface",
        "face_embeddings_adaface",
        "face_embeddings",
    ):
        assert database[name].deletes == [{"student_id": "student-01"}]


def test_두_모델_컬렉션에_학생별_unique_index를_만든다() -> None:
    database = RecordingDatabase()

    MongoFaceEmbeddingRepository.ensure_indexes(database)  # type: ignore[arg-type]

    for model_name in ("arcface", "adaface"):
        collection = database[f"face_embeddings_{model_name}"]
        assert any(
            fields == [("student_id", 1)] and options.get("unique") is True
            for fields, options in collection.indexes
        )


def test_지원하지_않는_모델은_어느_컬렉션에도_저장하지_않는다() -> None:
    database = RecordingDatabase()
    repository = MongoFaceEmbeddingRepository(database)  # type: ignore[arg-type]

    with pytest.raises(RepositoryDataError):
        repository.save(embedding("cosface"))

    assert all(not collection.updates for collection in database.collections.values())
