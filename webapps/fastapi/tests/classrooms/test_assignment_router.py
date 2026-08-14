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
    SeatAssignment,
    SeatCurrentOccupancy,
    SeatOccupancy,
)
from app.classrooms.service import ClassroomService
from app.main import app
from app.shared.adapters.memory_student_lookup import InMemoryStudentLookup
from app.shared.dependencies import get_classroom_service
from app.shared.student_identity import StudentIdentity

_ASSIGNMENT_URL = "/api/v1/classrooms/cls-001/seats/seat-001/assignment"


def _build_service() -> ClassroomService:
    """강의실·좌석 2개와 활성/비활성 학생이 준비된 서비스를 만든다."""
    now = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)
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
        clock=lambda: now,
    )


@pytest.fixture
def service() -> ClassroomService:
    return _build_service()


@pytest.fixture
def client(service: ClassroomService) -> Iterator[TestClient]:
    app.dependency_overrides[get_classroom_service] = lambda: service
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _seed_historical_assignment(service: ClassroomService, student_id: str, seat_id: str) -> None:
    """assign_student 검증을 거치지 않고 historical 지정을 심는다 (missing/inactive 케이스용).

    active가 아닌 학생은 write 경로로 지정할 수 없으므로, 목록 조회의
    historical blank-name 동작만 검증하기 위해 repository에 직접 심는다.
    """
    assert service._assignment_repository is not None
    service._assignment_repository.assign(
        SeatAssignment(
            seat_id=seat_id,
            student_id=student_id,
            classroom_id="cls-001",
            assigned_at=datetime(2026, 8, 13, 9, 0, tzinfo=UTC),
        )
    )


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

    def test_assign_inactive_student_returns_422(self, client: TestClient) -> None:
        """비활성 학생을 지정하면 422 STUDENT_INACTIVE_FOR_ASSIGNMENT를 돌려준다."""
        response = client.put(_ASSIGNMENT_URL, json={"student_id": "stu-002"})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "STUDENT_INACTIVE_FOR_ASSIGNMENT"

    def test_assign_moves_student_to_another_seat(self, client: TestClient) -> None:
        """같은 강의실의 다른 좌석으로 이동하면 200을 돌려준다."""
        first = client.put(
            "/api/v1/classrooms/cls-001/seats/seat-001/assignment",
            json={"student_id": "stu-001"},
        )
        assert first.status_code == 200

        moved = client.put(
            "/api/v1/classrooms/cls-001/seats/seat-002/assignment",
            json={"student_id": "stu-001"},
        )
        assert moved.status_code == 200
        body = moved.json()
        assert body["seat_id"] == "seat-002"
        assert body["student_id"] == "stu-001"
        assert body["student_name"] == "홍길동"
        assert "assigned_at" in body

        listing = client.get("/api/v1/classrooms/cls-001/seat-assignments")
        assert listing.status_code == 200
        assert len(listing.json()["items"]) == 1
        assert listing.json()["items"][0]["seat_id"] == "seat-002"

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

    def test_list_assignments_blank_name_for_missing_historical(
        self, service: ClassroomService, client: TestClient
    ) -> None:
        """lookup에 없는 학생의 historical 지정은 name blank 200을 돌려준다."""
        _seed_historical_assignment(service, "stu-404", "seat-001")
        response = client.get("/api/v1/classrooms/cls-001/seat-assignments")
        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 1
        assert body["items"][0]["student_id"] == "stu-404"
        assert body["items"][0]["student_name"] == ""
        assert body["items"][0]["assigned_at"] is not None

    def test_list_assignments_blank_name_for_inactive_historical(
        self, service: ClassroomService, client: TestClient
    ) -> None:
        """inactive 학생의 historical 지정은 name blank 200을 돌려준다."""
        _seed_historical_assignment(service, "stu-002", "seat-001")
        response = client.get("/api/v1/classrooms/cls-001/seat-assignments")
        assert response.status_code == 200
        body = response.json()
        assert len(body["items"]) == 1
        assert body["items"][0]["student_id"] == "stu-002"
        assert body["items"][0]["student_name"] == ""
        assert body["items"][0]["assigned_at"] is not None
