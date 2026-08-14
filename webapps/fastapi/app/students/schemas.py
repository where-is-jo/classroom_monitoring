"""학생 등록 HTTP 스키마."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from .models import CreateStudentCommand, RegisterStudentFaceCommand, Student


class CreateStudentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    student_number: str = Field(min_length=1, max_length=30)
    birth_date: date
    classroom_name: str = Field(min_length=1, max_length=50)
    phone: str | None = Field(default=None, max_length=20)
    guardian_phone: str = Field(min_length=1, max_length=20)
    face_enrollment_id: str | None = Field(default=None, max_length=100)

    def to_command(self) -> CreateStudentCommand:
        return CreateStudentCommand(
            student_number=self.student_number,
            name=self.name,
            birth_date=self.birth_date,
            classroom_name=self.classroom_name,
            phone=self.phone,
            guardian_phone=self.guardian_phone,
            face_enrollment_id=self.face_enrollment_id,
        )


class StudentResponse(BaseModel):
    id: str
    student_number: str
    name: str
    classroom_name: str
    face_registered: bool
    created_at: datetime

    @classmethod
    def from_domain(cls, student: Student) -> StudentResponse:
        return cls(
            id=student.id,
            student_number=student.student_number,
            name=student.name,
            classroom_name=student.classroom_name,
            face_registered=student.face_registered,
            created_at=student.created_at,
        )


class RegisterStudentFaceRequest(BaseModel):
    enrollment_id: str = Field(min_length=1, max_length=100)

    def to_command(self, student_id: str) -> RegisterStudentFaceCommand:
        return RegisterStudentFaceCommand(student_id=student_id, enrollment_id=self.enrollment_id)
