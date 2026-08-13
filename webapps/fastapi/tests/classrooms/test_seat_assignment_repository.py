"""좌석-학생 지정 저장소 테스트."""

from datetime import UTC, datetime

import pytest

from app.classrooms.adapters.memory_repository import InMemoryClassroomRepository
from app.classrooms.models import SeatAssignment


@pytest.fixture
def repository() -> InMemoryClassroomRepository:
    return InMemoryClassroomRepository()


class TestSeatAssignmentRepository:
    def test_assign_success(self, repository: InMemoryClassroomRepository) -> None:
        now = datetime.now(UTC)
        assignment = SeatAssignment(
            seat_id="seat-001",
            student_id="stu-001",
            classroom_id="cls-001",
            assigned_at=now,
        )
        result = repository.assign(assignment)
        assert result.seat_id == "seat-001"
        assert result.student_id == "stu-001"

    def test_get_by_seat(self, repository: InMemoryClassroomRepository) -> None:
        now = datetime.now(UTC)
        assignment = SeatAssignment(
            seat_id="seat-001",
            student_id="stu-001",
            classroom_id="cls-001",
            assigned_at=now,
        )
        repository.assign(assignment)
        result = repository.get_assignment_by_seat("seat-001")
        assert result is not None
        assert result.student_id == "stu-001"

    def test_get_by_student(self, repository: InMemoryClassroomRepository) -> None:
        now = datetime.now(UTC)
        assignment = SeatAssignment(
            seat_id="seat-001",
            student_id="stu-001",
            classroom_id="cls-001",
            assigned_at=now,
        )
        repository.assign(assignment)
        result = repository.get_assignment_by_student("stu-001", "cls-001")
        assert result is not None
        assert result.seat_id == "seat-001"

    def test_unassign(self, repository: InMemoryClassroomRepository) -> None:
        now = datetime.now(UTC)
        assignment = SeatAssignment(
            seat_id="seat-001",
            student_id="stu-001",
            classroom_id="cls-001",
            assigned_at=now,
        )
        repository.assign(assignment)
        repository.unassign("seat-001")
        result = repository.get_assignment_by_seat("seat-001")
        assert result is None

    def test_idempotent_assign(self, repository: InMemoryClassroomRepository) -> None:
        now = datetime.now(UTC)
        assignment1 = SeatAssignment(
            seat_id="seat-001",
            student_id="stu-001",
            classroom_id="cls-001",
            assigned_at=now,
        )
        assignment2 = SeatAssignment(
            seat_id="seat-001",
            student_id="stu-001",
            classroom_id="cls-001",
            assigned_at=now,
        )
        repository.assign(assignment1)
        repository.assign(assignment2)
        result = repository.get_assignment_by_seat("seat-001")
        assert result is not None

    def test_unassign_by_student(self, repository: InMemoryClassroomRepository) -> None:
        now = datetime.now(UTC)
        repository.assign(SeatAssignment("seat-001", "stu-001", "cls-001", now))
        repository.assign(SeatAssignment("seat-002", "stu-001", "cls-001", now))
        count = repository.unassign_by_student("stu-001")
        assert count == 2
        assert repository.get_assignment_by_seat("seat-001") is None
        assert repository.get_assignment_by_seat("seat-002") is None

    def test_list_by_classroom(self, repository: InMemoryClassroomRepository) -> None:
        now = datetime.now(UTC)
        repository.assign(SeatAssignment("seat-001", "stu-001", "cls-001", now))
        repository.assign(SeatAssignment("seat-002", "stu-002", "cls-001", now))
        repository.assign(SeatAssignment("seat-003", "stu-003", "cls-002", now))
        result = repository.list_assignments_by_classroom("cls-001")
        assert len(result) == 2
