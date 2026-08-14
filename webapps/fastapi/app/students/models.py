"""학생 원장 도메인 값."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Student:
    id: str
    student_number: str
    name: str
    birth_date: date
    classroom_name: str
    phone: str | None
    guardian_phone: str
    face_enrollment_id: str | None
    face_registered: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class CreateStudentCommand:
    student_number: str
    name: str
    birth_date: date
    classroom_name: str
    phone: str | None
    guardian_phone: str
    face_enrollment_id: str | None


@dataclass(frozen=True)
class RegisterStudentFaceCommand:
    student_id: str
    enrollment_id: str
