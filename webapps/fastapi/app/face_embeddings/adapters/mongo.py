"""학생 대표 얼굴 embedding MongoDB 저장소."""

from datetime import datetime
from typing import ClassVar

from pymongo import ASCENDING, ReturnDocument
from pymongo.collection import Collection
from pymongo.errors import PyMongoError

from ...shared.database import MongoDatabase, MongoDocument, document_id
from ...shared.errors import RepositoryDataError, RepositoryUnavailableError
from ..models import FaceEmbedding


class MongoFaceEmbeddingRepository:
    collection_names: ClassVar[dict[str, str]] = {
        "arcface": "face_embeddings_arcface",
        "adaface": "face_embeddings_adaface",
    }
    legacy_collection_name = "face_embeddings"

    def __init__(self, database: MongoDatabase) -> None:
        self._collections = {
            model_name: database[collection_name]
            for model_name, collection_name in self.collection_names.items()
        }
        self._legacy_collection = database[self.legacy_collection_name]

    @staticmethod
    def ensure_indexes(database: MongoDatabase) -> None:
        for model_name, collection_name in MongoFaceEmbeddingRepository.collection_names.items():
            collection = database[collection_name]
            collection.create_index(
                [("student_id", ASCENDING)],
                name=f"uq_face_embeddings_{model_name}_student_id",
                unique=True,
            )
            collection.create_index(
                [("student_number", ASCENDING)],
                name=f"ix_face_embeddings_{model_name}_student_number",
            )

    def save(self, embedding: FaceEmbedding) -> FaceEmbedding:
        collection = self._collection_for(embedding.model_name)
        values = _to_document(embedding)
        created_at = values.pop("created_at")
        values.pop("_id")
        try:
            document = collection.find_one_and_update(
                {"student_id": embedding.student_id},
                {
                    "$set": values,
                    "$setOnInsert": {"_id": embedding.id, "created_at": created_at},
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        if document is None:
            raise RepositoryUnavailableError()
        return _to_domain(document)

    def find_by_student(self, student_id: str, model_name: str) -> FaceEmbedding | None:
        try:
            document = self._collection_for(model_name).find_one({"student_id": student_id})
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else _to_domain(document)

    def list_student_ids(self, model_name: str) -> set[str]:
        try:
            documents = self._collection_for(model_name).find({}, {"_id": 0, "student_id": 1})
            student_ids: set[str] = set()
            for document in documents:
                student_id = document.get("student_id")
                if not isinstance(student_id, str) or not student_id:
                    raise RepositoryDataError()
                student_ids.add(student_id)
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return student_ids

    def delete_by_student(self, student_id: str) -> None:
        try:
            for collection in self._collections.values():
                collection.delete_one({"student_id": student_id})
            self._legacy_collection.delete_one({"student_id": student_id})
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def _collection_for(self, model_name: str) -> Collection[MongoDocument]:
        try:
            return self._collections[model_name]
        except KeyError:
            raise RepositoryDataError() from None


def _to_document(value: FaceEmbedding) -> MongoDocument:
    return {
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


def _to_domain(document: MongoDocument) -> FaceEmbedding:
    try:
        vector_value = document["vector"]
        if not isinstance(vector_value, list):
            raise TypeError
        vector = tuple(float(item) for item in vector_value)
        created_at = document["created_at"]
        updated_at = document["updated_at"]
        if not isinstance(created_at, datetime) or created_at.tzinfo is None:
            raise TypeError
        if not isinstance(updated_at, datetime) or updated_at.tzinfo is None:
            raise TypeError
        return FaceEmbedding(
            id=document_id(document),
            student_id=_string(document, "student_id"),
            student_name=_string(document, "student_name"),
            student_number=_string(document, "student_number"),
            enrollment_id=_string(document, "enrollment_id"),
            vector=vector,
            dimension=_integer(document, "dimension"),
            normalized=_boolean(document, "normalized"),
            model_name=_string(document, "model_name"),
            model_version=_string(document, "model_version"),
            preprocessing_version=_string(document, "preprocessing_version"),
            source_sample_count=_integer(document, "source_sample_count"),
            created_at=created_at,
            updated_at=updated_at,
        )
    except (KeyError, TypeError, ValueError):
        raise RepositoryDataError() from None


def _string(document: MongoDocument, key: str) -> str:
    value = document[key]
    if not isinstance(value, str) or not value:
        raise TypeError
    return value


def _integer(document: MongoDocument, key: str) -> int:
    value = document[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError
    return value


def _boolean(document: MongoDocument, key: str) -> bool:
    value = document[key]
    if not isinstance(value, bool):
        raise TypeError
    return value
