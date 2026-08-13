"""학생 조회 HTTP schema."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from .models import Student, StudentPage


class StudentCreateRequest(BaseModel):
    """학생 생성 요청."""

    student_no: str
    name: str
    department: str


class StudentUpdateRequest(BaseModel):
    """학생 수정 요청."""

    name: str | None = None
    department: str | None = None
    is_active: bool | None = None


class StudentResponse(BaseModel):
    """학생 응답."""

    id: str
    student_no: str
    name: str
    department: str
    is_active: bool
    created_at: datetime

    @classmethod
    def from_domain(cls, student: Student) -> StudentResponse:
        return cls(
            id=student.id,
            student_no=student.student_no,
            name=student.name,
            department=student.department,
            is_active=student.is_active,
            created_at=student.created_at,
        )


class StudentListResponse(BaseModel):
    """학생 목록 응답."""

    items: list[StudentResponse]
    total: int
    limit: int
    offset: int

    @classmethod
    def from_page(cls, page: StudentPage, limit: int, offset: int) -> StudentListResponse:
        return cls(
            items=[StudentResponse.from_domain(item) for item in page.items],
            total=page.total,
            limit=limit,
            offset=offset,
        )
