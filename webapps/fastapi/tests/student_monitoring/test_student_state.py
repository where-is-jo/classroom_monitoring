"""최근 탐지·카메라 ROI·좌석 지정 기반 학생 상태 테스트."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.classrooms.adapters.memory_repository import (
    InMemoryClassroomRepository,
    InMemorySeatAssignmentRepository,
)
from app.classrooms.errors import ClassroomNotFoundError
from app.classrooms.models import (
    CreateClassroomCommand,
    CreateSeatCommand,
    SeatAssignment,
    SeatGeometry,
)
from app.classrooms.service import ClassroomService
from app.main import app
from app.roi_connections.adapters.ffmpeg_camera import UnavailableCameraFrameGrabber
from app.roi_connections.adapters.memory import InMemoryRoiConnectionRepository
from app.roi_connections.models import Point, RoiConnection
from app.roi_connections.service import RoiConnectionService
from app.shared.adapters.memory_student_lookup import InMemoryStudentLookup
from app.shared.broadcaster import InMemoryBroadcaster
from app.shared.dependencies import get_student_monitoring_service
from app.shared.student_identity import StudentIdentity
from app.student_monitoring.adapters.memory_repository import (
    MemoryDetectionEventRepository,
    MemoryStudentStateRepository,
    MemoryVideoSegmentRepository,
)
from app.student_monitoring.models import (
    Detection,
    DetectionEvent,
    FrameInfo,
    StudentState,
    StudentStateReason,
)
from app.student_monitoring.service import StudentMonitoringService
from app.video_monitoring.adapters.memory_repository import MemoryVideoStreamRepository
from app.video_monitoring.errors import VideoStreamNotFoundError
from app.video_monitoring.models import PlaybackKind, VideoStream

CLASSROOM_ID = "classroom-a101"
NOW = datetime(2026, 8, 13, 9, 10, tzinfo=UTC)
RECENT = datetime(2026, 8, 13, 9, 9, tzinfo=UTC)


@dataclass(frozen=True)
class StateContext:
    service: StudentMonitoringService
    detection_repository: MemoryDetectionEventRepository
    assignment_repository: InMemorySeatAssignmentRepository
    roi_repository: InMemoryRoiConnectionRepository
    broadcaster: InMemoryBroadcaster


def _student(
    student_id: str, student_no: str, name: str, *, active: bool = True
) -> StudentIdentity:
    return StudentIdentity(
        id=student_id,
        student_no=student_no,
        name=name,
        is_active=active,
    )


def _build_context(*, assign_two_students: bool = True) -> StateContext:
    student_lookup = InMemoryStudentLookup(
        identities=(
            _student("student-1", "20260001", "김로운"),
            _student("student-2", "20260002", "박우현"),
            _student("student-inactive", "20260003", "비활성", active=False),
        )
    )
    classroom_repository = InMemoryClassroomRepository()
    assignment_repository = InMemorySeatAssignmentRepository(classroom_repository)
    classroom_service = ClassroomService(
        classroom_repository,
        student_lookup=student_lookup,
        assignment_repository=assignment_repository,
        occupancy_confidence_threshold=0.6,
        clock=lambda: NOW,
    )
    classroom_service.seed_classroom(
        CreateClassroomCommand(
            id=CLASSROOM_ID,
            code="A101",
            name="A101 일반 강의실",
            location="A동 1층",
        )
    )
    # 역순으로 넣어도 상태 응답은 좌석 코드 순서여야 한다.
    for seat_id, code, x in (("seat-2", "S02", 0.5), ("seat-1", "S01", 0.1)):
        classroom_service.seed_seat(
            CreateSeatCommand(
                id=seat_id,
                classroom_id=CLASSROOM_ID,
                code=code,
                label=f"좌석 {code}",
                geometry=SeatGeometry(x=x, y=0.1, width=0.2, height=0.2),
            )
        )
    classroom_service.assign_student("seat-1", "student-1")
    if assign_two_students:
        classroom_service.assign_student("seat-2", "student-2")

    stream_repository = MemoryVideoStreamRepository()
    stream_repository.save(
        VideoStream(
            id="stream-camera-a",
            camera_id="camera-a",
            classroom_id=CLASSROOM_ID,
            camera_label="Left Camera",
            playback_kind=PlaybackKind.WEBRTC,
            playback_path="/webrtc/camera-a",
            enabled=True,
            last_frame_at=None,
            last_detection_at=None,
            is_demo=False,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    roi_repository = InMemoryRoiConnectionRepository()
    # ROI의 legacy student_id는 실제 assignment와 반대로 둔다. 판정은 이를 사용하면 안 된다.
    roi_repository.save(_roi("seat-1", _rectangle(0.1, 0.1, 0.3, 0.3), student_id="student-2"))
    roi_repository.save(_roi("seat-2", _rectangle(0.5, 0.1, 0.7, 0.3), student_id="student-1"))
    roi_service = RoiConnectionService(
        classroom_service,
        student_lookup,
        roi_repository,
        stream_repository,
        UnavailableCameraFrameGrabber(),
        max_upload_bytes=1024,
        page_size_max=200,
        clock=lambda: NOW,
    )
    detection_repository = MemoryDetectionEventRepository()
    broadcaster = InMemoryBroadcaster()
    service = StudentMonitoringService(
        detection_repository=detection_repository,
        segment_repository=MemoryVideoSegmentRepository(),
        stream_repository=stream_repository,
        state_repository=MemoryStudentStateRepository(),
        broadcaster=broadcaster,
        classroom_service=classroom_service,
        roi_service=roi_service,
        occupancy_confidence_threshold=0.6,
        occupancy_hold_seconds=0,
        identity_confidence_threshold=0.7,
        stale_seconds=300,
        identity_hold_seconds=0,
        absent_grace_seconds=300,
        history_limit=50,
        clock=lambda: NOW,
        student_lookup=student_lookup,
    )
    return StateContext(
        service=service,
        detection_repository=detection_repository,
        assignment_repository=assignment_repository,
        roi_repository=roi_repository,
        broadcaster=broadcaster,
    )


def _rectangle(left: float, top: float, right: float, bottom: float) -> tuple[Point, ...]:
    return (
        Point(left, top),
        Point(right, top),
        Point(right, bottom),
        Point(left, bottom),
    )


def _roi(
    seat_id: str,
    polygon: tuple[Point, ...],
    *,
    student_id: str | None = None,
) -> RoiConnection:
    return RoiConnection(
        classroom_id=CLASSROOM_ID,
        camera_id="camera-a",
        seat_id=seat_id,
        student_id=student_id,
        polygon=polygon,
        reference_image_revision=0,
        updated_at=NOW,
    )


def _person(
    detection_id: str,
    bbox: tuple[int, int, int, int],
    *,
    student_id: str = "student-1",
    confidence: float = 0.95,
    identity_confidence: float = 0.9,
) -> Detection:
    return Detection(
        detection_id=detection_id,
        class_id=0,
        class_name="person",
        confidence=confidence,
        bbox=bbox,
        student_id=student_id,
        identity_confidence=identity_confidence,
        face_bbox=None,
    )


def _event(
    event_id: str,
    detections: tuple[Detection, ...],
    *,
    captured_at: datetime = RECENT,
    camera_id: str = "camera-a",
) -> DetectionEvent:
    return DetectionEvent(
        event_id=event_id,
        camera_id=camera_id,
        stream_id=f"stream-{camera_id}",
        classroom_id=CLASSROOM_ID,
        captured_at=captured_at,
        sequence=1,
        frame=FrameInfo(width_pixels=1000, height_pixels=1000),
        detections=detections,
        received_at=captured_at,
        schema_version=1,
    )


def _save(context: StateContext, event: DetectionEvent) -> None:
    """탐지 이벤트를 서비스 경로로 넣는다.

    판정이 조회에서 수신으로 옮겨졌으므로, 저장소에 직접 넣으면 상태가 만들어지지
    않는다. 조회가 판정하지 않는다는 것이 결정 0008이 요구한 동작이다.
    """
    context.service.receive_inference_event(event)


def test_list_includes_all_assigned_students_and_unobserved_unknown() -> None:
    context = _build_context()
    _save(context, _event("event-1", (_person("det-1", (150, 150, 250, 250)),)))

    states = context.service.list_student_states(CLASSROOM_ID)

    assert [state.student_id for state in states] == ["student-1", "student-2"]
    present, unknown = states
    assert present.student_name == "김로운"
    assert present.student_no == "20260001"
    assert present.assigned_seat_label == "좌석 S01"
    assert present.current_seat_id == "seat-1"
    assert present.current_state == StudentState.PRESENT
    assert present.confidence == 0.9
    assert present.last_observed_at == RECENT
    assert unknown.current_state == StudentState.UNKNOWN
    assert unknown.confidence is None
    # 판정이 수신 시점으로 옮겨져, 식별하지 못한 학생도 "언제 본 프레임으로 판정했는지"가
    # 남는다. 관측 자체는 있었고 신원만 없었다는 사실을 이 값이 구분해 준다.
    assert unknown.last_observed_at == RECENT
    assert unknown.reason == StudentStateReason.SEAT_VACANT_WITHIN_GRACE


def test_assignment_is_truth_even_when_roi_legacy_student_differs() -> None:
    context = _build_context(assign_two_students=False)
    _save(context, _event("event-1", (_person("det-1", (150, 150, 250, 250)),)))

    state = context.service.list_student_states(CLASSROOM_ID)[0]

    assert state.student_id == "student-1"
    assert state.current_state == StudentState.PRESENT


def test_detection_on_other_roi_is_wrong_seat() -> None:
    context = _build_context(assign_two_students=False)
    _save(context, _event("event-1", (_person("det-1", (550, 150, 650, 250)),)))

    state = context.service.list_student_states(CLASSROOM_ID)[0]

    assert state.current_seat_id == "seat-2"
    assert state.current_state == StudentState.WRONG_SEAT


@pytest.mark.parametrize(
    ("event", "expected_reason"),
    [
        (
            _event(
                "stale",
                (_person("det-1", (150, 150, 250, 250)),),
                captured_at=datetime(2026, 8, 13, 9, 4, 59, tzinfo=UTC),
            ),
            StudentStateReason.SEAT_NOT_OBSERVED,
        ),
        (
            _event(
                "low-detection",
                (_person("det-1", (150, 150, 250, 250), confidence=0.59),),
            ),
            # 사람은 잡혔지만 확신이 없다. 좌석은 UNKNOWN이고 학생도 판정하지 않는다.
            StudentStateReason.SEAT_NOT_OBSERVED,
        ),
        (
            _event(
                "low-identity",
                (_person("det-1", (150, 150, 250, 250), identity_confidence=0.69),),
            ),
            # 누군가 앉아 있지만 그가 누구인지 확신할 수 없다. 이름을 붙이지 않는다.
            StudentStateReason.SEAT_OCCUPIED_BY_UNKNOWN,
        ),
    ],
    ids=("stale", "low-detection", "low-identity"),
)
def test_stale_or_low_confidence_evidence_is_unknown(
    event: DetectionEvent, expected_reason: StudentStateReason
) -> None:
    context = _build_context(assign_two_students=False)
    _save(context, event)

    state = context.service.list_student_states(CLASSROOM_ID)[0]

    assert state.current_state == StudentState.UNKNOWN
    assert state.reason == expected_reason
    assert state.current_seat_id is None
    assert state.confidence is None


def test_identified_outside_every_roi_is_in_classroom() -> None:
    """누군지 아는 사람이 좌석 밖에 있다는 것은 "모른다"와 다른 사실이다.

    결정 0025의 7번이 `IN_CLASSROOM`을 MVP 범위로 올렸다. 신원 없는 좌석 밖 탐지는
    여전히 아무의 상태도 바꾸지 않는다.
    """
    context = _build_context(assign_two_students=False)
    _save(context, _event("outside", (_person("det-1", (800, 800, 900, 900)),)))

    state = context.service.list_student_states(CLASSROOM_ID)[0]

    assert state.current_state == StudentState.IN_CLASSROOM
    assert state.reason == StudentStateReason.IDENTIFIED_OUTSIDE_SEATS
    assert state.current_seat_id is None
    assert state.confidence == 0.9
    assert state.last_observed_at == RECENT


def test_overlapping_rois_are_unknown() -> None:
    context = _build_context(assign_two_students=False)
    context.roi_repository.save(
        _roi("seat-2", _rectangle(0.1, 0.1, 0.3, 0.3), student_id="student-1")
    )
    _save(context, _event("event-overlap", (_person("det-1", (150, 150, 250, 250)),)))

    state = context.service.list_student_states(CLASSROOM_ID)[0]

    assert state.current_state == StudentState.UNKNOWN
    assert state.current_seat_id is None


def test_same_event_selects_highest_identity_then_detection_confidence() -> None:
    context = _build_context(assign_two_students=False)
    _save(
        context,
        _event(
            "event-choice",
            (
                _person(
                    "det-present",
                    (150, 150, 250, 250),
                    confidence=0.99,
                    identity_confidence=0.8,
                ),
                _person(
                    "det-wrong",
                    (550, 150, 650, 250),
                    confidence=0.7,
                    identity_confidence=0.9,
                ),
            ),
        ),
    )

    state = context.service.list_student_states(CLASSROOM_ID)[0]

    assert state.current_state == StudentState.WRONG_SEAT
    assert state.confidence == 0.9


def test_newest_valid_event_wins_over_older_event() -> None:
    context = _build_context(assign_two_students=False)
    _save(
        context,
        _event(
            "event-old",
            (_person("det-old", (150, 150, 250, 250)),),
            captured_at=datetime(2026, 8, 13, 9, 8, tzinfo=UTC),
        ),
    )
    _save(
        context,
        _event(
            "event-new",
            (_person("det-new", (550, 150, 650, 250)),),
            captured_at=RECENT,
        ),
    )

    state = context.service.list_student_states(CLASSROOM_ID)[0]

    assert state.current_state == StudentState.WRONG_SEAT
    assert state.last_observed_at == RECENT


def test_inactive_student_and_broken_seat_assignments_are_excluded() -> None:
    context = _build_context(assign_two_students=False)
    context.assignment_repository.assign(
        SeatAssignment("seat-2", "student-inactive", CLASSROOM_ID, NOW)
    )
    context.assignment_repository.assign(
        SeatAssignment("missing-seat", "student-2", CLASSROOM_ID, NOW)
    )

    states = context.service.list_student_states(CLASSROOM_ID)

    assert [state.student_id for state in states] == ["student-1"]


def test_missing_camera_reference_is_rejected_and_changes_no_state() -> None:
    """등록되지 않은 카메라의 이벤트로는 어떤 학생 상태도 만들지 않는다."""
    context = _build_context(assign_two_students=False)

    with pytest.raises(VideoStreamNotFoundError):
        context.service.receive_inference_event(
            _event(
                "event-missing-camera",
                (_person("det-1", (150, 150, 250, 250)),),
                camera_id="missing-camera",
            )
        )

    state = context.service.list_student_states(CLASSROOM_ID)[0]

    assert state.current_state == StudentState.UNKNOWN
    assert state.last_observed_at is None


def test_get_read_model_does_not_publish_sse() -> None:
    context = _build_context(assign_two_students=False)
    queue = context.broadcaster.subscribe()
    _save(context, _event("event-1", (_person("det-1", (150, 150, 250, 250)),)))
    # 수신이 발행한 것은 비운다. 여기서 보려는 것은 "조회가 더 발행하는가"다.
    while not queue.empty():
        queue.get_nowait()

    context.service.list_student_states(CLASSROOM_ID)

    assert queue.empty()


def test_new_event_publishes_safe_detection_labels_and_student_state_once() -> None:
    context = _build_context(assign_two_students=False)
    queue = context.broadcaster.subscribe()
    event = _event(
        "event-sse",
        (
            _person("det-active", (150, 150, 250, 250)),
            _person(
                "det-low",
                (150, 150, 250, 250),
                confidence=0.59,
            ),
            _person(
                "det-missing",
                (150, 150, 250, 250),
                student_id="student-missing",
            ),
        ),
    )

    first = context.service.receive_inference_event(event)
    second = context.service.receive_inference_event(event)
    published: list[dict[str, object]] = []
    while not queue.empty():
        published.append(queue.get_nowait())

    assert first.is_new is True
    assert second.is_new is False
    detection_events = [item for item in published if item["type"] == "detection"]
    student_events = [item for item in published if item["type"] == "student-state"]
    assert len(detection_events) == 1
    assert len(student_events) == 1
    detections = detection_events[0]["detections"]
    assert isinstance(detections, list)
    assert [item["display_label"] for item in detections] == ["김로운", "사람", "사람"]
    assert all("identity_confidence" not in item for item in detections)
    assert all("face_bbox" not in item for item in detections)
    assert student_events[0] == {
        "type": "student-state",
        "event_id": "event-sse",
        "classroom_id": CLASSROOM_ID,
        "student_id": "student-1",
        "student_name": "김로운",
        "student_no": "20260001",
        "assigned_seat_id": "seat-1",
        "assigned_seat_label": "좌석 S01",
        "current_seat_id": "seat-1",
        "current_seat_label": "좌석 S01",
        "current_state": "PRESENT",
        "reason": "IDENTIFIED_AT_ASSIGNED_SEAT",
        "confidence": 0.9,
        "observed_at": "2026-08-13T09:09:00+00:00",
    }


def test_missing_classroom_raises_not_found() -> None:
    context = _build_context()

    with pytest.raises(ClassroomNotFoundError):
        context.service.list_student_states("classroom-missing")


def test_student_states_endpoint_preserves_response_contract() -> None:
    context = _build_context(assign_two_students=False)
    _save(context, _event("event-1", (_person("det-1", (150, 150, 250, 250)),)))
    app.dependency_overrides[get_student_monitoring_service] = lambda: context.service
    try:
        response = TestClient(app).get(f"/api/v1/classrooms/{CLASSROOM_ID}/student-states")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "classroom_id": CLASSROOM_ID,
        "states": [
            {
                "student_id": "student-1",
                "student_name": "김로운",
                "student_no": "20260001",
                "assigned_seat_id": "seat-1",
                "assigned_seat_label": "좌석 S01",
                "current_seat_id": "seat-1",
                "current_seat_label": "좌석 S01",
                "current_state": "PRESENT",
                "reason": "IDENTIFIED_AT_ASSIGNED_SEAT",
                "confidence": 0.9,
                "last_observed_at": "2026-08-13T09:09:00Z",
            }
        ],
    }


def test_student_states_endpoint_keeps_missing_classroom_envelope() -> None:
    context = _build_context()
    app.dependency_overrides[get_student_monitoring_service] = lambda: context.service
    try:
        response = TestClient(app).get("/api/v1/classrooms/classroom-missing/student-states")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CLASSROOM_NOT_FOUND"


def test_state_transitions_are_recorded_as_history() -> None:
    """상태가 바뀐 순간의 근거를 되짚을 수 있어야 한다(결정 0008)."""
    context = _build_context(assign_two_students=False)
    _save(context, _event("event-present", (_person("det-1", (150, 150, 250, 250)),)))
    _save(
        context,
        _event(
            "event-wrong",
            (_person("det-2", (550, 150, 650, 250)),),
            captured_at=datetime(2026, 8, 13, 9, 9, 30, tzinfo=UTC),
        ),
    )

    history = context.service.list_student_state_history(CLASSROOM_ID, "student-1")

    assert [(item.from_state, item.to_state) for item in history] == [
        (StudentState.PRESENT, StudentState.WRONG_SEAT),
        (StudentState.UNKNOWN, StudentState.PRESENT),
    ]
    assert history[0].event_id == "event-wrong"
    assert history[0].reason == StudentStateReason.IDENTIFIED_AT_OTHER_SEAT
    assert history[0].seat_id == "seat-2"


def test_unchanged_state_does_not_pile_up_history() -> None:
    """같은 상태가 이어지는 동안 이력이 프레임마다 쌓이면 근거를 찾을 수 없다."""
    context = _build_context(assign_two_students=False)
    for index in range(3):
        _save(
            context,
            _event(
                f"event-{index}",
                (_person(f"det-{index}", (150, 150, 250, 250)),),
                captured_at=datetime(2026, 8, 13, 9, 9, index, tzinfo=UTC),
            ),
        )

    history = context.service.list_student_state_history(CLASSROOM_ID, "student-1")

    assert len(history) == 1
    assert history[0].to_state == StudentState.PRESENT


def test_old_event_does_not_revert_newer_state() -> None:
    """늦게 도착한 오래된 프레임이 최신 판정을 되돌리지 않는다."""
    context = _build_context(assign_two_students=False)
    _save(
        context,
        _event(
            "event-new",
            (_person("det-new", (550, 150, 650, 250)),),
            captured_at=RECENT,
        ),
    )
    _save(
        context,
        _event(
            "event-late",
            (_person("det-late", (150, 150, 250, 250)),),
            captured_at=datetime(2026, 8, 13, 9, 8, tzinfo=UTC),
        ),
    )

    state = context.service.list_student_states(CLASSROOM_ID)[0]

    assert state.current_state == StudentState.WRONG_SEAT
    assert state.last_observed_at == RECENT
