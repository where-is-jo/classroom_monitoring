"""좌석-학생 지정 서비스 테스트."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.classrooms.adapters.memory_repository import (
    InMemoryClassroomRepository,
    InMemorySeatAssignmentRepository,
)
from app.classrooms.errors import (
    ClassroomInputError,
    SeatInactiveForAssignmentError,
    StudentInactiveForAssignmentError,
)
from app.classrooms.models import (
    Classroom,
    OccupancySource,
    Seat,
    SeatAssignment,
    SeatCurrentOccupancy,
    SeatOccupancy,
)
from app.classrooms.service import ClassroomService
from app.shared.adapters.memory_student_lookup import InMemoryStudentLookup
from app.shared.errors import StudentNotFoundError
from app.shared.student_identity import StudentIdentity


@pytest.fixture
def service() -> ClassroomService:
    """활성 강의실·좌석 2개와 활성 학생 1명을 가진 서비스를 만든다."""
    now = datetime.now(UTC)
    repository = InMemoryClassroomRepository()
    student_lookup = InMemoryStudentLookup(
        identities=(
            StudentIdentity(
                id="stu-001",
                student_no="20260101",
                name="홍길동",
                is_active=True,
            ),
            StudentIdentity(
                id="stu-002",
                student_no="20269999",
                name="김비활성",
                is_active=False,
            ),
            StudentIdentity(
                id="stu-003",
                student_no="20260203",
                name="이지원",
                is_active=True,
            ),
        )
    )
    assignment_repo = InMemorySeatAssignmentRepository()

    repository.create_classroom(
        Classroom(
            id="cls-001",
            code="R101",
            name="강의실1",
            location="본관",
            is_active=True,
            created_at=now,
        )
    )
    for seat_id, seat_code, label in (
        ("seat-001", "S01", "좌석 1"),
        ("seat-002", "S02", "좌석 2"),
    ):
        repository.create_seat(
            Seat(
                id=seat_id,
                classroom_id="cls-001",
                code=seat_code,
                label=label,
                geometry=None,
                is_active=True,
                current_occupancy=SeatCurrentOccupancy(
                    state=SeatOccupancy.UNKNOWN,
                    source=OccupancySource.SYSTEM,
                    confidence=None,
                    observed_at=None,
                    event_id=None,
                ),
                created_at=now,
                updated_at=now,
                version=0,
            )
        )

    return ClassroomService(
        repository,
        student_lookup=student_lookup,
        assignment_repository=assignment_repo,
        occupancy_confidence_threshold=0.6,
        clock=lambda: datetime.now(UTC),
    )


class TestSeatAssignment:
    def test_assign_student(self, service: ClassroomService) -> None:
        """좌석에 학생을 지정하면 학생 정보가 함께 반환된다."""
        info = service.assign_student("seat-001", "stu-001")
        assert info.seat_id == "seat-001"
        assert info.student_id == "stu-001"
        assert info.student_name == "홍길동"
        assert info.student_no == "20260101"
        assert info.seat_label == "좌석 1"

    def test_assign_idempotent(self, service: ClassroomService) -> None:
        """같은 좌석에 같은 학생을 다시 지정해도 지정이 하나만 유지된다."""
        first = service.assign_student("seat-001", "stu-001")
        second = service.assign_student("seat-001", "stu-001")
        assert first.seat_id == second.seat_id == "seat-001"
        assert first.student_id == second.student_id == "stu-001"
        assert len(service.list_assignments("cls-001")) == 1

    def test_assign_replaces_student_on_seat(self, service: ClassroomService) -> None:
        """한 좌석에 다른 학생을 지정하면 기존 학생 지정을 교체한다."""
        service.assign_student("seat-001", "stu-001")
        info = service.assign_student("seat-001", "stu-003")
        assert info.student_id == "stu-003"
        assignments = service.list_assignments("cls-001")
        assert len(assignments) == 1
        assert assignments[0].student_id == "stu-003"

    def test_assign_moves_student_to_another_seat(self, service: ClassroomService) -> None:
        """같은 강의실의 다른 좌석에 지정하면 기존 지정을 해제하고 이동한다."""
        service.assign_student("seat-001", "stu-001")
        info = service.assign_student("seat-002", "stu-001")
        assert info.seat_id == "seat-002"
        assignments = service.list_assignments("cls-001")
        assert len(assignments) == 1
        assert assignments[0].seat_id == "seat-002"

    def test_assign_inactive_seat_raises(self, service: ClassroomService) -> None:
        """비활성화된 좌석에는 지정할 수 없다."""
        service.update_seat("seat-002", is_active=False)
        with pytest.raises(SeatInactiveForAssignmentError):
            service.assign_student("seat-002", "stu-001")

    def test_assign_inactive_student_raises(self, service: ClassroomService) -> None:
        """비활성화된 학생은 지정할 수 없다."""
        with pytest.raises(StudentInactiveForAssignmentError):
            service.assign_student("seat-001", "stu-002")

    def test_assign_unknown_student_raises(self, service: ClassroomService) -> None:
        """존재하지 않는 학생은 지정할 수 없다."""
        with pytest.raises(StudentNotFoundError):
            service.assign_student("seat-001", "stu-404")

    def test_assign_without_assignment_repository_raises(self) -> None:
        """지정 저장소가 연결되지 않으면 입력 오류를 던진다."""
        bare_service = ClassroomService(
            InMemoryClassroomRepository(),
            occupancy_confidence_threshold=0.6,
            clock=lambda: datetime.now(UTC),
        )
        with pytest.raises(ClassroomInputError):
            bare_service.assign_student("seat-001", "stu-001")

    def test_list_assignments(self, service: ClassroomService) -> None:
        """지정 현황 목록이 학생·좌석 정보와 함께 반환된다."""
        service.assign_student("seat-001", "stu-001")
        assignments = service.list_assignments("cls-001")
        assert len(assignments) == 1
        assert assignments[0].student_name == "홍길동"
        assert assignments[0].seat_label == "좌석 1"

    def test_list_assignments_blank_name_for_missing_historical(
        self, service: ClassroomService
    ) -> None:
        """lookup에 없는 학생의 historical 지정은 blank name으로 표시된다."""
        assert service._assignment_repository is not None
        service._assignment_repository.assign(
            SeatAssignment(
                seat_id="seat-001",
                student_id="stu-404",
                classroom_id="cls-001",
                assigned_at=datetime.now(UTC),
            )
        )
        assignments = service.list_assignments("cls-001")
        assert len(assignments) == 1
        assert assignments[0].student_id == "stu-404"
        assert assignments[0].student_name == ""
        assert assignments[0].student_no == ""

    def test_list_assignments_blank_name_for_inactive_historical(
        self, service: ClassroomService
    ) -> None:
        """inactive 학생의 historical 지정은 blank name으로 표시된다."""
        assert service._assignment_repository is not None
        service._assignment_repository.assign(
            SeatAssignment(
                seat_id="seat-001",
                student_id="stu-002",
                classroom_id="cls-001",
                assigned_at=datetime.now(UTC),
            )
        )
        assignments = service.list_assignments("cls-001")
        assert len(assignments) == 1
        assert assignments[0].student_id == "stu-002"
        assert assignments[0].student_name == ""
        assert assignments[0].student_no == ""

    def test_unassign_student(self, service: ClassroomService) -> None:
        """지정을 해제하면 목록에서 사라진다."""
        service.assign_student("seat-001", "stu-001")
        service.unassign_student("seat-001")
        assert service.list_assignments("cls-001") == []

    def test_unassign_by_student(self, service: ClassroomService) -> None:
        """학생의 좌석 지정을 해제하고 해제 수를 반환한다."""
        service.assign_student("seat-001", "stu-001")
        count = service.unassign_by_student("stu-001")
        assert count == 1
        assert service.list_assignments("cls-001") == []
