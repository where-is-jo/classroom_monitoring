"""학생 MongoDB 문서와 인덱스 계약 테스트."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import cast

import pytest
from pymongo.errors import DuplicateKeyError

from app.shared.database import MongoDatabase
from app.students.adapters.mongo import MongoStudentRepository
from app.students.errors import StudentDuplicateError
from app.students.models import Student


class RecordingCollection:
    def __init__(self) -> None:
        self.indexes: list[tuple[list[tuple[str, int]], dict[str, object]]] = []
        self.documents: list[dict[str, object]] = []
        self.duplicate = False

    def create_index(self, fields: list[tuple[str, int]], **options: object) -> None:
        self.indexes.append((fields, options))

    def insert_one(self, document: dict[str, object]) -> None:
        if self.duplicate:
            raise DuplicateKeyError("duplicate")
        self.documents.append(document)


class RecordingDatabase:
    def __init__(self) -> None:
        self.collection = RecordingCollection()

    def __getitem__(self, name: str) -> RecordingCollection:
        assert name == "students"
        return self.collection


def student() -> Student:
    now = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
    return Student(
        id="550e8400-e29b-41d4-a716-446655440000",
        student_number="ST-001",
        name="김민지",
        birth_date=date(2012, 5, 3),
        classroom_name="중등 수학 A반",
        phone="010-1234-5678",
        guardian_phone="010-9876-5432",
        face_enrollment_id="enrollment-001",
        face_registered=True,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def test_indexes_and_document_columns() -> None:
    database = RecordingDatabase()
    mongo_database = cast(MongoDatabase, database)
    MongoStudentRepository.ensure_indexes(mongo_database)
    repository = MongoStudentRepository(mongo_database)

    repository.create(student())

    index_names = {options["name"] for _, options in database.collection.indexes}
    assert index_names == {"uq_students_student_number", "ix_students_classroom_active"}
    document = database.collection.documents[0]
    assert document == {
        "_id": "550e8400-e29b-41d4-a716-446655440000",
        "student_number": "ST-001",
        "name": "김민지",
        "birth_date": "2012-05-03",
        "classroom_name": "중등 수학 A반",
        "phone": "010-1234-5678",
        "guardian_phone": "010-9876-5432",
        "face_enrollment_id": "enrollment-001",
        "face_registered": True,
        "is_active": True,
        "created_at": datetime(2026, 8, 14, 9, 0, tzinfo=UTC),
        "updated_at": datetime(2026, 8, 14, 9, 0, tzinfo=UTC),
    }


def test_duplicate_key_is_domain_conflict() -> None:
    database = RecordingDatabase()
    database.collection.duplicate = True
    repository = MongoStudentRepository(cast(MongoDatabase, database))

    with pytest.raises(StudentDuplicateError):
        repository.create(student())
