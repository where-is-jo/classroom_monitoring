"""학생 서비스 테스트."""

from datetime import UTC, datetime

import pytest

from app.students.errors import StudentDuplicateError, StudentNotFoundError
from app.students.service import StudentService


@pytest.fixture
def service() -> StudentService:
    from app.students.adapters.memory_repository import InMemoryStudentRepository

    return StudentService(InMemoryStudentRepository(), clock=lambda: datetime.now(UTC))


class TestStudentService:
    def test_create_student(self, service: StudentService) -> None:
        student = service.create_student("20240001", "김철수", "컴퓨터공학과")
        assert student.student_no == "20240001"
        assert student.name == "김철수"
        assert student.is_active is True

    def test_duplicate_student_no(self, service: StudentService) -> None:
        service.create_student("20240001", "김철수", "컴퓨터공학과")
        with pytest.raises(StudentDuplicateError):
            service.create_student("20240001", "이영희", "컴퓨터공학과")

    def test_get_student(self, service: StudentService) -> None:
        created = service.create_student("20240001", "김철수", "컴퓨터공학과")
        found = service.get_student(created.id)
        assert found.id == created.id

    def test_get_nonexistent_student(self, service: StudentService) -> None:
        with pytest.raises(StudentNotFoundError):
            service.get_student("nonexistent")

    def test_update_student(self, service: StudentService) -> None:
        created = service.create_student("20240001", "김철수", "컴퓨터공학과")
        updated = service.update_student(created.id, name="김철수영")
        assert updated.name == "김철수영"

    def test_deactivate_student(self, service: StudentService) -> None:
        created = service.create_student("20240001", "김철수", "컴퓨터공학과")
        service.deactivate_student(created.id)
        found = service.get_student(created.id)
        assert found.is_active is False
