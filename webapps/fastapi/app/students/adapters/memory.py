"""학생 원장 메모리 저장소."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from threading import RLock

from ...shared.student_identity import StudentIdentity, StudentIdentityPage
from ..errors import StudentDuplicateError
from ..models import Student


class InMemoryStudentRepository:
    def __init__(self, students: tuple[Student, ...] = ()) -> None:
        self._students = {student.id: student for student in students}
        self._lock = RLock()

    def create(self, student: Student) -> Student:
        with self._lock:
            if student.id in self._students or any(
                saved.student_number == student.student_number for saved in self._students.values()
            ):
                raise StudentDuplicateError()
            self._students[student.id] = student
        return student

    def get_student(self, student_id: str) -> Student | None:
        with self._lock:
            return self._students.get(student_id)

    def list_students(self, *, limit: int, offset: int) -> list[Student]:
        with self._lock:
            items = sorted(
                self._students.values(), key=lambda student: student.created_at, reverse=True
            )
        return items[offset : offset + limit]

    def register_face(
        self, student_id: str, enrollment_id: str, updated_at: datetime
    ) -> Student | None:
        with self._lock:
            student = self._students.get(student_id)
            if student is None:
                return None
            saved = replace(
                student,
                face_enrollment_id=enrollment_id,
                face_registered=True,
                updated_at=updated_at,
            )
            self._students[student_id] = saved
            return saved

    def find_by_id(self, student_id: str) -> StudentIdentity | None:
        student = self.get_student(student_id)
        return None if student is None else _to_identity(student)

    def list_active(self, *, limit: int, offset: int) -> StudentIdentityPage:
        with self._lock:
            active = sorted(
                (student for student in self._students.values() if student.is_active),
                key=lambda student: student.id,
            )
        return StudentIdentityPage(
            items=[_to_identity(student) for student in active[offset : offset + limit]],
            total=len(active),
        )


def _to_identity(student: Student) -> StudentIdentity:
    return StudentIdentity(
        id=student.id,
        student_no=student.student_number,
        name=student.name,
        is_active=student.is_active,
    )
