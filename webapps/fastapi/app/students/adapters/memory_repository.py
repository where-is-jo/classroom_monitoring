"""메모리 학생 저장소."""

from __future__ import annotations

from ..errors import StudentDuplicateError, StudentNotFoundError
from ..models import Student, StudentPage


class InMemoryStudentRepository:
    """dict 기반 in-memory 학생 저장소."""

    def __init__(self) -> None:
        self._students: dict[str, Student] = {}
        self._by_student_no: dict[str, str] = {}  # student_no -> student_id

    def create(self, student: Student) -> Student:
        if student.student_no in self._by_student_no:
            raise StudentDuplicateError()
        self._students[student.id] = student
        self._by_student_no[student.student_no] = student.id
        return student

    def get_by_id(self, student_id: str) -> Student | None:
        return self._students.get(student_id)

    def get_by_student_no(self, student_no: str) -> Student | None:
        student_id = self._by_student_no.get(student_no)
        if student_id is None:
            return None
        return self._students.get(student_id)

    def list_students(
        self, *, limit: int, offset: int, is_active: bool | None = None
    ) -> StudentPage:
        items = list(self._students.values())
        if is_active is not None:
            items = [s for s in items if s.is_active == is_active]
        total = len(items)
        items = items[offset : offset + limit]
        return StudentPage(items=items, total=total)

    def update(self, student: Student) -> Student:
        existing = self._students.get(student.id)
        if existing is None:
            raise StudentNotFoundError()
        # 학번 변경 시 중복 검사
        if student.student_no != existing.student_no:
            if student.student_no in self._by_student_no:
                raise StudentDuplicateError()
            del self._by_student_no[existing.student_no]
            self._by_student_no[student.student_no] = student.id
        self._students[student.id] = student
        return student

    def count_by_classroom(self, classroom_id: str) -> int:
        # 현재 이 메서드는 나중에 SeatAssignmentRepository에서 구현
        # 임시로 0 반환
        return 0
