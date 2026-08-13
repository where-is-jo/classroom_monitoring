"""학생 저장소 포트."""

from __future__ import annotations

from typing import Protocol

from .models import Student, StudentPage


class StudentRepository(Protocol):
    """학생 저장소 추상화."""

    def create(self, student: Student) -> Student:
        """학생을 저장한다."""
        ...

    def get_by_id(self, student_id: str) -> Student | None:
        """ID로 학생을 조회한다."""
        ...

    def get_by_student_no(self, student_no: str) -> Student | None:
        """학번으로 학생을 조회한다."""
        ...

    def list_students(
        self, *, limit: int, offset: int, is_active: bool | None = None
    ) -> StudentPage:
        """학생 목록을 조회한다."""
        ...

    def update(self, student: Student) -> Student:
        """학생 정보를 갱신한다."""
        ...

    def count_by_classroom(self, classroom_id: str) -> int:
        """특정 강의실에 지정된 학생 수를 반환한다."""
        ...
