"""학생 도메인 모델."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Student:
    """학생 원장."""

    id: str
    student_no: str  # 학번 (고유)
    name: str  # 이름
    department: str  # 소속
    is_active: bool  # 활성 여부
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class StudentPage:
    """학생 목록 페이지네이션 결과."""

    items: list[Student]
    total: int


@dataclass(frozen=True)
class CreateStudentCommand:
    """학생 생성 명령."""

    student_no: str
    name: str
    department: str


@dataclass(frozen=True)
class UpdateStudentCommand:
    """학생 수정 명령."""

    student_id: str
    name: str | None
    department: str | None
    is_active: bool | None
