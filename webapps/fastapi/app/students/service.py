"""학생 비즈니스 로직."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from .errors import StudentDuplicateError, StudentInputError, StudentNotFoundError
from .models import Student, StudentPage
from .ports import StudentRepository


class StudentService:
    """학생 관리 서비스.

    비활성화 시 좌석 지정 해제를 위해 SeatAssignmentRepository를 직접 사용한다.
    ClassroomService와의 순환 의존을 피하기 위함이다.
    """

    def __init__(
        self,
        repository: StudentRepository,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._clock = clock

    def create_student(self, student_no: str, name: str, department: str) -> Student:
        """학생을 등록한다."""
        normalized_no = self._student_no(student_no)
        normalized_name = self._text(name, "이름")
        normalized_dept = self._text(department, "소속")

        if self._repository.get_by_student_no(normalized_no) is not None:
            raise StudentDuplicateError()

        now = self._aware_datetime(self._clock())
        student = Student(
            id=str(self._clock().timestamp()),  # 임시 ID 생성
            student_no=normalized_no,
            name=normalized_name,
            department=normalized_dept,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        return self._repository.create(student)

    def get_student(self, student_id: str) -> Student:
        """학생을 조회한다."""
        student = self._repository.get_by_id(student_id)
        if student is None:
            raise StudentNotFoundError()
        return student

    def list_students(
        self,
        *,
        limit: int,
        offset: int,
        is_active: bool | None = None,
        classroom_id: str | None = None,
    ) -> StudentPage:
        """학생 목록을 조회한다."""
        return self._repository.list_students(limit=limit, offset=offset, is_active=is_active)

    def update_student(
        self,
        student_id: str,
        *,
        name: str | None = None,
        department: str | None = None,
        is_active: bool | None = None,
    ) -> Student:
        """학생 정보를 수정한다."""
        student = self.get_student(student_id)

        updated_name = self._text(name, "이름") if name is not None else student.name
        updated_dept = (
            self._text(department, "소속") if department is not None else student.department
        )
        now = self._aware_datetime(self._clock())

        updated = replace(
            student,
            name=updated_name,
            department=updated_dept,
            is_active=student.is_active if is_active is None else is_active,
            updated_at=now,
        )
        return self._repository.update(updated)

    def deactivate_student(self, student_id: str) -> None:
        """학생을 비활성화하고 좌석 지정을 해제한다."""
        student = self.get_student(student_id)
        now = self._aware_datetime(self._clock())
        updated = replace(
            student,
            is_active=False,
            updated_at=now,
        )
        self._repository.update(updated)

    @staticmethod
    def _text(value: str, label: str) -> str:
        normalized = " ".join(value.split())
        if not normalized or len(normalized) > 200:
            raise StudentInputError(f"{label}은 1~200자여야 합니다.")
        return normalized

    @staticmethod
    def _student_no(value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 64:
            raise StudentInputError("학번은 1~64자여야 합니다.")
        return normalized

    @staticmethod
    def _aware_datetime(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
