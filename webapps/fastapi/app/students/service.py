"""학생 등록 비즈니스 규칙."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from .errors import StudentInputError, StudentNotFoundError
from .models import CreateStudentCommand, RegisterStudentFaceCommand, Student
from .ports import StudentRepository

_STUDENT_NUMBER_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_PHONE_PATTERN = re.compile(r"^[0-9+() -]+$")


def _generate_student_id() -> str:
    return str(uuid4())


class StudentService:
    def __init__(
        self,
        repository: StudentRepository,
        *,
        clock: Callable[[], datetime],
        id_factory: Callable[[], str] = _generate_student_id,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._id_factory = id_factory

    def create(self, command: CreateStudentCommand) -> Student:
        student_number = _required_text(command.student_number, "학생 번호", 30)
        if not _STUDENT_NUMBER_PATTERN.fullmatch(student_number):
            raise StudentInputError("학생 번호는 영문, 숫자, 하이픈, 밑줄만 사용할 수 있습니다.")
        name = _required_text(command.name, "학생 이름", 50)
        classroom_name = _required_text(command.classroom_name, "소속 반", 50)
        guardian_phone = _phone(command.guardian_phone, "보호자 연락처", required=True)
        phone = _phone(command.phone, "학생 연락처", required=False)
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("학생 등록 시각은 timezone-aware 값이어야 합니다.")
        if command.birth_date > now.date():
            raise StudentInputError("생년월일은 오늘 이후일 수 없습니다.")
        face_enrollment_id = _optional_text(command.face_enrollment_id, 100)
        return self._repository.create(
            Student(
                id=self._id_factory(),
                student_number=student_number,
                name=name,
                birth_date=command.birth_date,
                classroom_name=classroom_name,
                phone=phone,
                guardian_phone=guardian_phone or "",
                face_enrollment_id=face_enrollment_id,
                face_registered=face_enrollment_id is not None,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )

    def list_students(self, *, limit: int) -> list[Student]:
        return self._repository.list_students(limit=limit, offset=0)

    def get_student(self, student_id: str) -> Student:
        student = self._repository.get_student(student_id)
        if student is None:
            raise StudentNotFoundError()
        return student

    def register_face(self, command: RegisterStudentFaceCommand) -> Student:
        enrollment_id = _required_text(command.enrollment_id, "얼굴 등록 ID", 100)
        student = self._repository.register_face(command.student_id, enrollment_id, self._clock())
        if student is None:
            raise StudentNotFoundError()
        return student

    def unregister_face(self, student_id: str) -> Student | None:
        """얼굴 등록 표시를 되돌린다. 학생이 없으면 조용히 넘어간다.

        동의 철회 경로에서 불린다. 학생이 이미 지워졌더라도 embedding 삭제는 이미
        끝났으므로, 여기서 실패로 만들면 되돌릴 수 없는 것을 실패로 보고하게 된다.
        """
        return self._repository.unregister_face(student_id, self._clock())


def _required_text(value: str, label: str, max_length: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise StudentInputError(f"{label}을(를) 입력해 주세요.")
    if len(normalized) > max_length:
        raise StudentInputError(f"{label}은(는) {max_length}자 이하여야 합니다.")
    return normalized


def _optional_text(value: str | None, max_length: int) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = value.strip()
    if len(normalized) > max_length:
        raise StudentInputError(f"얼굴 등록 ID는 {max_length}자 이하여야 합니다.")
    return normalized


def _phone(value: str | None, label: str, *, required: bool) -> str | None:
    normalized = "" if value is None else value.strip()
    if not normalized:
        if required:
            raise StudentInputError(f"{label}을(를) 입력해 주세요.")
        return None
    if len(normalized) > 20 or not _PHONE_PATTERN.fullmatch(normalized):
        raise StudentInputError(f"{label} 형식이 올바르지 않습니다.")
    digits = "".join(character for character in normalized if character.isdigit())
    if len(digits) < 7:
        raise StudentInputError(f"{label} 형식이 올바르지 않습니다.")
    return normalized
