"""학생 상태 판정 테스트."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.classrooms.adapters.memory_repository import InMemoryClassroomRepository
from app.classrooms.errors import ClassroomNotFoundError
from app.classrooms.models import (
    CreateClassroomCommand,
    CreateSeatCommand,
    SeatAssignment,
    SeatGeometry,
)
from app.classrooms.service import ClassroomService
from app.main import app
from app.shared.broadcaster import InMemoryBroadcaster
from app.shared.dependencies import get_student_monitoring_service
from app.student_monitoring.adapters.memory_repository import (
    MemoryDetectionEventRepository,
    MemoryVideoSegmentRepository,
)
from app.student_monitoring.models import (
    Detection,
    FrameInfo,
    StudentSeatState,
    StudentState,
)
from app.student_monitoring.service import StudentMonitoringService
from app.students.adapters.memory_repository import InMemoryStudentRepository
from app.students.models import Student
from app.video_monitoring.adapters.memory_repository import MemoryVideoStreamRepository

_CLASSROOM_ID = "classroom-a101"


def _clock() -> datetime:
    return datetime(2026, 8, 13, 9, 0, tzinfo=UTC)


def _classroom_service() -> ClassroomService:
    service = ClassroomService(
        InMemoryClassroomRepository(),
        occupancy_confidence_threshold=0.5,
        clock=_clock,
    )
    service.seed_classroom(
        CreateClassroomCommand(
            id=_CLASSROOM_ID,
            code="A101",
            name="A101 일반 강의실",
            location="A동 1층",
        )
    )
    # seat-1: [0.1, 0.3]x[0.1, 0.3], seat-2: [0.5, 0.7]x[0.1, 0.3] (정규화 좌표)
    service.seed_seat(
        CreateSeatCommand(
            id="seat-1",
            classroom_id=_CLASSROOM_ID,
            code="S01",
            label="좌석 1",
            geometry=SeatGeometry(x=0.1, y=0.1, width=0.2, height=0.2),
        )
    )
    service.seed_seat(
        CreateSeatCommand(
            id="seat-2",
            classroom_id=_CLASSROOM_ID,
            code="S02",
            label="좌석 2",
            geometry=SeatGeometry(x=0.5, y=0.1, width=0.2, height=0.2),
        )
    )
    return service


def _make_service(
    *,
    student_repository: InMemoryStudentRepository | None = None,
) -> StudentMonitoringService:
    return StudentMonitoringService(
        detection_repository=MemoryDetectionEventRepository(),
        segment_repository=MemoryVideoSegmentRepository(),
        stream_repository=MemoryVideoStreamRepository(),
        broadcaster=InMemoryBroadcaster(),
        classroom_service=_classroom_service(),
        occupancy_confidence_threshold=0.5,
        identity_confidence_threshold=0.5,
        student_repository=student_repository,
    )


def _person(
    detection_id: str,
    bbox: tuple[int, int, int, int],
    *,
    student_id: str | None,
    identity_confidence: float | None = 0.9,
) -> Detection:
    return Detection(
        detection_id=detection_id,
        class_id=0,
        class_name="person",
        confidence=0.95,
        bbox=bbox,
        student_id=student_id,
        identity_confidence=identity_confidence,
        face_bbox=None,
    )


def _assignment(student_id: str, seat_id: str) -> SeatAssignment:
    return SeatAssignment(
        seat_id=seat_id,
        student_id=student_id,
        classroom_id=_CLASSROOM_ID,
        assigned_at=_clock(),
    )


def _judge(
    service: StudentMonitoringService,
    detections: tuple[Detection, ...],
    *,
    assignments: tuple[SeatAssignment, ...] = (),
) -> list[StudentSeatState]:
    seats = service._classroom_service.list_all_seats(_CLASSROOM_ID)
    return service._judge_student_states(
        detections=detections,
        seats=seats,
        assignments=assignments,
        frame=FrameInfo(width_pixels=1000, height_pixels=1000),
    )


class TestStudentStateJudgment:
    """StudentState 기본 값 단위 테스트."""

    def test_student_state_present(self) -> None:
        """지정 좌석에 탐지 → PRESENT."""
        # 간단한 단위 테스트
        assert StudentState.PRESENT.value == "PRESENT"

    def test_student_state_wrong_seat(self) -> None:
        """다른 좌석에 탐지 → WRONG_SEAT."""
        assert StudentState.WRONG_SEAT.value == "WRONG_SEAT"

    def test_student_state_unknown(self) -> None:
        """student_id null → UNKNOWN."""
        assert StudentState.UNKNOWN.value == "UNKNOWN"


class TestJudgeStudentStates:
    """_judge_student_states 판정 규칙 테스트 (REQ-009~012, R9)."""

    def test_present_when_detected_on_assigned_seat(self) -> None:
        """지정 좌석에 탐지된 학생은 PRESENT로 판정한다 (REQ-010)."""
        service = _make_service()
        result = _judge(
            service,
            (_person("det-1", (150, 150, 250, 250), student_id="s1"),),
            assignments=(_assignment("s1", "seat-1"),),
        )

        assert len(result) == 1
        state = result[0]
        assert state.student_id == "s1"
        assert state.assigned_seat_id == "seat-1"
        assert state.current_seat_id == "seat-1"
        assert state.current_state == StudentState.PRESENT
        assert state.confidence == 0.95
        assert state.last_observed_at is None

    def test_wrong_seat_when_detected_on_other_seat(self) -> None:
        """지정 좌석이 아닌 다른 좌석에 탐지되면 WRONG_SEAT로 판정한다 (REQ-011)."""
        service = _make_service()
        result = _judge(
            service,
            (_person("det-1", (550, 150, 650, 250), student_id="s1"),),
            assignments=(_assignment("s1", "seat-1"),),
        )

        assert len(result) == 1
        state = result[0]
        assert state.current_seat_id == "seat-2"
        assert state.current_state == StudentState.WRONG_SEAT

    def test_unknown_when_not_on_any_seat(self) -> None:
        """탐지 위치가 어느 좌석에도 속하지 않으면 UNKNOWN으로 판정한다."""
        service = _make_service()
        result = _judge(
            service,
            (_person("det-1", (850, 150, 950, 250), student_id="s1"),),
            assignments=(_assignment("s1", "seat-1"),),
        )

        assert len(result) == 1
        state = result[0]
        assert state.current_seat_id is None
        assert state.current_state == StudentState.UNKNOWN

    def test_null_student_id_is_excluded(self) -> None:
        """student_id가 null인 탐지는 결과에 포함되지 않는다 (REQ-012)."""
        service = _make_service()
        result = _judge(
            service,
            (_person("det-1", (150, 150, 250, 250), student_id=None),),
            assignments=(_assignment("s1", "seat-1"),),
        )

        assert result == []

    def test_below_identity_confidence_threshold_is_excluded(self) -> None:
        """identity_confidence가 임계값 미만인 탐지는 결과에 포함되지 않는다 (R9)."""
        service = _make_service()
        result = _judge(
            service,
            (
                _person(
                    "det-1",
                    (150, 150, 250, 250),
                    student_id="s1",
                    identity_confidence=0.3,
                ),
            ),
            assignments=(_assignment("s1", "seat-1"),),
        )

        assert result == []

    def test_student_without_assignment_is_excluded(self) -> None:
        """지정 좌석이 없는 학생은 결과에 포함되지 않는다."""
        service = _make_service()
        result = _judge(
            service,
            (_person("det-1", (150, 150, 250, 250), student_id="s2"),),
        )

        assert result == []

    def test_duplicate_student_keeps_first_detection(self) -> None:
        """같은 학생이 두 곳에 탐지되면 먼저 탐지된 쪽만 결과에 남긴다."""
        service = _make_service()
        result = _judge(
            service,
            (
                _person("det-1", (150, 150, 250, 250), student_id="s1"),
                _person("det-2", (550, 150, 650, 250), student_id="s1"),
            ),
            assignments=(_assignment("s1", "seat-1"),),
        )

        assert len(result) == 1
        assert result[0].current_seat_id == "seat-1"
        assert result[0].current_state == StudentState.PRESENT

    def test_student_name_and_seat_label_resolved(self) -> None:
        """학생 저장소와 좌석 라벨이 결과에 채워진다."""
        student_repository = InMemoryStudentRepository()
        student_repository.create(
            Student(
                id="s1",
                student_no="20260001",
                name="홍길동",
                department="컴퓨터공학과",
                is_active=True,
                created_at=_clock(),
                updated_at=_clock(),
            )
        )
        service = _make_service(student_repository=student_repository)
        result = _judge(
            service,
            (_person("det-1", (150, 150, 250, 250), student_id="s1"),),
            assignments=(_assignment("s1", "seat-1"),),
        )

        assert len(result) == 1
        assert result[0].student_name == "홍길동"
        assert result[0].student_no == "20260001"
        assert result[0].assigned_seat_label == "좌석 1"


class TestListStudentStates:
    """list_student_states 진입점 테스트."""

    def test_returns_empty_list_for_existing_classroom(self) -> None:
        """좌석·지정이 있어도 TASK-A05 구현은 빈 목록을 반환한다."""
        service = _make_service()
        assert service.list_student_states(_CLASSROOM_ID) == []

    def test_missing_classroom_raises_not_found(self) -> None:
        """존재하지 않는 강의실은 ClassroomNotFoundError를 발생시킨다."""
        service = _make_service()
        with pytest.raises(ClassroomNotFoundError):
            service.list_student_states("classroom-missing")


class TestStudentStatesEndpoint:
    """GET /api/v1/classrooms/{classroom_id}/student-states 엔드포인트 테스트."""

    def test_missing_classroom_returns_404(self) -> None:
        """존재하지 않는 강의실은 404로 응답한다."""
        service = _make_service()
        app.dependency_overrides[get_student_monitoring_service] = lambda: service
        try:
            client = TestClient(app)
            response = client.get("/api/v1/classrooms/classroom-missing/student-states")
            assert response.status_code == 404
        finally:
            app.dependency_overrides.clear()

    def test_existing_classroom_returns_empty_states(self) -> None:
        """존재하는 강의실은 빈 states 목록을 200으로 응답한다."""
        service = _make_service()
        app.dependency_overrides[get_student_monitoring_service] = lambda: service
        try:
            client = TestClient(app)
            response = client.get(f"/api/v1/classrooms/{_CLASSROOM_ID}/student-states")
            assert response.status_code == 200
            data = response.json()
            assert data["classroom_id"] == _CLASSROOM_ID
            assert data["states"] == []
        finally:
            app.dependency_overrides.clear()
