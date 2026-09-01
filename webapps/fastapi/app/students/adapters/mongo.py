"""학생 원장 MongoDB 저장소."""

from __future__ import annotations

from datetime import date, datetime

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from ...shared.database import (
    MongoDatabase,
    MongoDocument,
    document_id,
    document_id_filter,
)
from ...shared.errors import RepositoryDataError, RepositoryUnavailableError
from ...shared.student_identity import StudentIdentity, StudentIdentityPage
from ..errors import StudentDuplicateError
from ..models import Student


class MongoStudentRepository:
    collection_name = "students"

    def __init__(self, database: MongoDatabase) -> None:
        self._collection = database[self.collection_name]

    @staticmethod
    def ensure_indexes(database: MongoDatabase) -> None:
        collection = database[MongoStudentRepository.collection_name]
        collection.create_index(
            [("student_number", ASCENDING)], name="uq_students_student_number", unique=True
        )
        collection.create_index(
            [("classroom_name", ASCENDING), ("is_active", ASCENDING)],
            name="ix_students_classroom_active",
        )

    def create(self, student: Student) -> Student:
        try:
            self._collection.insert_one(_to_document(student))
        except DuplicateKeyError:
            raise StudentDuplicateError() from None
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return student

    def get_student(self, student_id: str) -> Student | None:
        try:
            document = self._collection.find_one(document_id_filter(student_id))
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else _to_domain(document)

    def list_students(self, *, limit: int, offset: int) -> list[Student]:
        try:
            documents = list(
                self._collection.find({}).sort("created_at", DESCENDING).skip(offset).limit(limit)
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return [_to_domain(document) for document in documents]

    def register_face(
        self, student_id: str, enrollment_id: str, updated_at: datetime
    ) -> Student | None:
        try:
            document = self._collection.find_one_and_update(
                document_id_filter(student_id),
                {
                    "$set": {
                        "face_enrollment_id": enrollment_id,
                        "face_registered": True,
                        "updated_at": updated_at,
                    }
                },
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else _to_domain(document)

    def unregister_face(self, student_id: str, updated_at: datetime) -> Student | None:
        """얼굴 등록 표시를 되돌린다.

        **이것이 없어서 동의 철회가 서비스를 멈추게 했다.** embedding만 지우고
        `face_registered`를 참으로 남기면, 갤러리 완전성 검사가 "등록됐다는데 벡터가
        없는 학생"을 보고 전체 식별을 실패로 닫는다. 사람이 학생 문서를 손으로 고치기
        전까지 복구되지 않는다.
        """
        try:
            document = self._collection.find_one_and_update(
                document_id_filter(student_id),
                {
                    "$set": {
                        "face_enrollment_id": None,
                        "face_registered": False,
                        "updated_at": updated_at,
                    }
                },
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else _to_domain(document)

    def find_by_id(self, student_id: str) -> StudentIdentity | None:
        student = self.get_student(student_id)
        return None if student is None else _to_identity(student)

    def list_active(self, *, limit: int, offset: int) -> StudentIdentityPage:
        try:
            query = {"is_active": True}
            documents = list(
                self._collection.find(query).sort("_id", ASCENDING).skip(offset).limit(limit)
            )
            total = self._collection.count_documents(query)
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return StudentIdentityPage(
            items=[_to_identity(_to_domain(document)) for document in documents], total=total
        )


def _to_document(student: Student) -> MongoDocument:
    return {
        "_id": student.id,
        "student_number": student.student_number,
        "name": student.name,
        "birth_date": student.birth_date.isoformat(),
        "classroom_name": student.classroom_name,
        "phone": student.phone,
        "guardian_phone": student.guardian_phone,
        "face_enrollment_id": student.face_enrollment_id,
        "face_registered": student.face_registered,
        "is_active": student.is_active,
        "created_at": student.created_at,
        "updated_at": student.updated_at,
    }


def _to_domain(document: MongoDocument) -> Student:
    try:
        return Student(
            id=document_id(document),
            student_number=_required_str(document, "student_number"),
            name=_required_str(document, "name"),
            birth_date=date.fromisoformat(_required_str(document, "birth_date")),
            classroom_name=_required_str(document, "classroom_name"),
            phone=_optional_str(document, "phone"),
            guardian_phone=_required_str(document, "guardian_phone"),
            face_enrollment_id=_optional_str(document, "face_enrollment_id"),
            face_registered=_required_bool(document, "face_registered"),
            is_active=_required_bool(document, "is_active"),
            created_at=_required_datetime(document, "created_at"),
            updated_at=_required_datetime(document, "updated_at"),
        )
    except (KeyError, TypeError, ValueError):
        raise RepositoryDataError() from None


def _to_identity(student: Student) -> StudentIdentity:
    return StudentIdentity(
        id=student.id,
        student_no=student.student_number,
        name=student.name,
        is_active=student.is_active,
    )


def _required_str(document: MongoDocument, key: str) -> str:
    value = document[key]
    if not isinstance(value, str) or not value:
        raise TypeError
    return value


def _optional_str(document: MongoDocument, key: str) -> str | None:
    value = document.get(key)
    if value is not None and not isinstance(value, str):
        raise TypeError
    return value


def _required_bool(document: MongoDocument, key: str) -> bool:
    value = document[key]
    if not isinstance(value, bool):
        raise TypeError
    return value


def _required_datetime(document: MongoDocument, key: str) -> datetime:
    value = document[key]
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise TypeError
    return value
