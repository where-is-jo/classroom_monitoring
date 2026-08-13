"""학생 메모리 저장소 단위 테스트."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.students.adapters.memory_repository import InMemoryStudentRepository
from app.students.errors import StudentDuplicateError, StudentNotFoundError
from app.students.models import Student


def _student(
    student_id: str = "student-1",
    student_no: str = "20260101",
    name: str = "김학생",
    department: str = "컴퓨터공학과",
    is_active: bool = True,
) -> Student:
    return Student(
        id=student_id,
        student_no=student_no,
        name=name,
        department=department,
        is_active=is_active,
        created_at=datetime(2026, 8, 13, 9, 0, tzinfo=UTC),
        updated_at=datetime(2026, 8, 13, 9, 0, tzinfo=UTC),
    )


class TestInMemoryStudentRepositoryCreate:
    def test_create_and_find_by_id(self) -> None:
        repo = InMemoryStudentRepository()
        repo.create(_student())
        found = repo.get_by_id("student-1")
        assert found is not None
        assert found.student_no == "20260101"
        assert found.name == "김학생"

    def test_create_duplicate_student_no_raises(self) -> None:
        repo = InMemoryStudentRepository()
        repo.create(_student())
        with pytest.raises(StudentDuplicateError):
            repo.create(_student(student_id="student-2"))


class TestInMemoryStudentRepositoryFind:
    def test_find_by_student_no(self) -> None:
        repo = InMemoryStudentRepository()
        repo.create(_student())
        found = repo.get_by_student_no("20260101")
        assert found is not None
        assert found.id == "student-1"

    def test_find_nonexistent_returns_none(self) -> None:
        repo = InMemoryStudentRepository()
        assert repo.get_by_id("missing") is None
        assert repo.get_by_student_no("missing") is None


class TestInMemoryStudentRepositoryList:
    def test_list_returns_all_students(self) -> None:
        repo = InMemoryStudentRepository()
        repo.create(_student(student_id="student-1", student_no="20260101"))
        repo.create(_student(student_id="student-2", student_no="20260102"))
        page = repo.list_students(limit=10, offset=0)
        assert page.total == 2
        assert {s.id for s in page.items} == {"student-1", "student-2"}

    def test_list_with_offset_and_limit(self) -> None:
        repo = InMemoryStudentRepository()
        repo.create(_student(student_id="student-1", student_no="20260101"))
        repo.create(_student(student_id="student-2", student_no="20260102"))
        repo.create(_student(student_id="student-3", student_no="20260103"))
        page = repo.list_students(limit=1, offset=1)
        assert page.total == 3
        assert len(page.items) == 1
        assert page.items[0].id == "student-2"

    def test_list_filters_by_active(self) -> None:
        repo = InMemoryStudentRepository()
        repo.create(_student(student_id="student-1", student_no="20260101"))
        repo.create(_student(student_id="student-2", student_no="20260102", is_active=False))
        active_page = repo.list_students(limit=10, offset=0, is_active=True)
        inactive_page = repo.list_students(limit=10, offset=0, is_active=False)
        assert [s.id for s in active_page.items] == ["student-1"]
        assert [s.id for s in inactive_page.items] == ["student-2"]


class TestInMemoryStudentRepositoryUpdate:
    def test_update_fields(self) -> None:
        repo = InMemoryStudentRepository()
        repo.create(_student())
        updated = _student(name="이학생", department="전자공학과", is_active=False)
        result = repo.update(updated)
        assert result.name == "이학생"
        assert result.department == "전자공학과"
        assert result.is_active is False
        assert repo.get_by_id("student-1") == updated

    def test_update_nonexistent_raises(self) -> None:
        repo = InMemoryStudentRepository()
        with pytest.raises(StudentNotFoundError):
            repo.update(_student(student_id="missing"))

    def test_update_student_no_to_duplicate_raises(self) -> None:
        repo = InMemoryStudentRepository()
        repo.create(_student(student_id="student-1", student_no="20260101"))
        repo.create(_student(student_id="student-2", student_no="20260102"))
        with pytest.raises(StudentDuplicateError):
            repo.update(_student(student_id="student-2", student_no="20260101"))

    def test_update_student_no_keeps_lookup_in_sync(self) -> None:
        repo = InMemoryStudentRepository()
        repo.create(_student(student_id="student-1", student_no="20260101"))
        repo.update(_student(student_id="student-1", student_no="20269999"))
        assert repo.get_by_student_no("20260101") is None
        assert repo.get_by_student_no("20269999") is not None
