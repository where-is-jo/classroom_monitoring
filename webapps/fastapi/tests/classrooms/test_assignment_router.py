"""좌석-학생 지정 라우터 테스트."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.classrooms.adapters.memory_repository import (
    InMemoryClassroomRepository,
    InMemorySeatAssignmentRepository,
)
from app.classrooms.models import (
    Classroom,
    OccupancySource,
    Seat,
    SeatCurrentOccupancy,
    SeatOccupancy,
)
from app.classrooms.service import ClassroomService
from app.main import app
from app.shared.dependencies import get_classroom_service
from app.students.adapters.memory_repository import InMemoryStudentRepository
from app.students.models import Student

_ASSIGNMENT_URL = "/api/v1/classrooms/cls-001/seats/seat-001/assignment"


def _build_service() -> ClassroomService:
    """강의실·좌석·활성 학생이 준비된 서비스를 만든다."""
    now = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)
    repository = InMemoryClassroomRepository()
    student_repo = InMemoryStudentRepository()
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
    repository.create_seat(
        Seat(
            id="seat-001",
            classroom_id="cls-001",
            code="S01",
            label="좌석 1",
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
    student_repo.create(
        Student(
            id="stu-001",
            student_no="20260101",
            name="홍길동",
            department="컴퓨터공학과",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
    )
    return ClassroomService(
        repository,
        student_repository=student_repo,
        assignment_repository=assignment_repo,
        occupancy_confidence_threshold=0.6,
        clock=lambda: now,
    )


@pytest.fixture
def client() -> Iterator[TestClient]:
    service = _build_service()
    app.dependency_overrides[get_classroom_service] = lambda: service
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


class TestSeatAssignmentAPI:
    def test_assign_student(self, client: TestClient) -> None:
        """좌석에 학생을 지정하면 학생 정보를 포함한 200을 돌려준다."""
        response = client.put(_ASSIGNMENT_URL, json={"student_id": "stu-001"})
        assert response.status_code == 200
        body = response.json()
        assert body["seat_id"] == "seat-001"
        assert body["student_id"] == "stu-001"
        assert body["student_name"] == "홍길동"
        assert "assigned_at" in body

    def test_assign_idempotent(self, client: TestClient) -> None:
        """같은 지정을 두 번 요청해도 200을 유지한다."""
        first = client.put(_ASSIGNMENT_URL, json={"student_id": "stu-001"})
        second = client.put(_ASSIGNMENT_URL, json={"student_id": "stu-001"})
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["student_id"] == second.json()["student_id"]

    def test_assign_unknown_student_returns_404(self, client: TestClient) -> None:
        """존재하지 않는 학생을 지정하면 404를 돌려준다."""
        response = client.put(_ASSIGNMENT_URL, json={"student_id": "stu-404"})
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "STUDENT_NOT_FOUND"

    def test_assign_unknown_seat_returns_404(self, client: TestClient) -> None:
        """존재하지 않는 좌석을 지정하면 404를 돌려준다."""
        response = client.put(
            "/api/v1/classrooms/cls-001/seats/seat-999/assignment",
            json={"student_id": "stu-001"},
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "SEAT_NOT_FOUND"

    def test_unassign_student(self, client: TestClient) -> None:
        """지정을 해제하면 204를 돌려주고 목록에서 사라진다."""
        assert client.put(_ASSIGNMENT_URL, json={"student_id": "stu-001"}).status_code == 200
        response = client.delete(_ASSIGNMENT_URL)
        assert response.status_code == 204
        listing = client.get("/api/v1/classrooms/cls-001/seat-assignments")
        assert listing.status_code == 200
        assert listing.json()["items"] == []

    def test_list_assignments(self, client: TestClient) -> None:
        """지정 현황 목록을 items로 돌려준다."""
        client.put(_ASSIGNMENT_URL, json={"student_id": "stu-001"})
        response = client.get("/api/v1/classrooms/cls-001/seat-assignments")
        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 1
        assert body["items"][0]["student_name"] == "홍길동"
