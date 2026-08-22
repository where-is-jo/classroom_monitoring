"""학생 상태 판정을 실제 MongoDB에 대고 검증한다.

대역이 아니라 진짜 저장소를 쓴다. 메모리 저장소는 dict 하나라 직렬화·역직렬화가 없고
index도 없어서, 문서 모양이 틀렸거나 새 필드를 저장에서 빠뜨린 실수를 잡지 못한다.
[결정 0032](../../../../docs/architecture/decisions.md#0032--학생-상태-판정을-좌석-근거-하나에서-파생시키고-수신-시점에-저장한다)로
바뀐 경로가 MongoDB에서도 그대로 도는지 여기서 확인한다.

`TEST_DATABASE_URL`이 없으면 전부 skip된다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta

import pytest

from app.classrooms.adapters.mongo_repository import (
    MongoClassroomRepository,
    MongoSeatAssignmentRepository,
    MongoSeatMutationUnitOfWork,
)
from app.classrooms.models import (
    CreateClassroomCommand,
    CreateSeatCommand,
    SeatGeometry,
    SeatOccupancy,
)
from app.classrooms.service import ClassroomService
from app.roi_connections.adapters.ffmpeg_camera import UnavailableCameraFrameGrabber
from app.roi_connections.adapters.mongo import MongoRoiConnectionRepository
from app.roi_connections.models import Point, RoiConnection
from app.roi_connections.service import RoiConnectionService
from app.shared.broadcaster import InMemoryBroadcaster
from app.shared.database import MongoDatabase, initialize_indexes
from app.student_monitoring.adapters.mongo_repository import (
    MongoDetectionEventRepository,
    MongoStudentStateRepository,
    MongoVideoSegmentRepository,
)
from app.student_monitoring.models import (
    Detection,
    DetectionEvent,
    FrameInfo,
    StudentSeatState,
    StudentState,
    StudentStateReason,
)
from app.student_monitoring.service import StudentMonitoringService
from app.students.adapters.mongo import MongoStudentRepository
from app.students.models import Student
from app.video_monitoring.adapters.mongo_repository import MongoVideoStreamRepository
from app.video_monitoring.models import CameraRole, PlaybackKind, VideoStream

CLASSROOM_ID = "it-classroom-a"
CAMERA_ID = "it-camera-a"
STREAM_ID = "it-stream-a"
STUDENT_1 = "it-student-1"
STUDENT_2 = "it-student-2"
NOW = datetime(2026, 8, 21, 9, 0, tzinfo=UTC)
FRAME = FrameInfo(width_pixels=1000, height_pixels=1000)

# 좌석 1 ROI: [0.1, 0.3] x [0.1, 0.3] → 중심 (200, 200)
# 좌석 2 ROI: [0.5, 0.7] x [0.1, 0.3] → 중심 (600, 200)
SEAT_1_BBOX = (150, 150, 250, 250)
SEAT_2_BBOX = (550, 150, 650, 250)
OUTSIDE_BBOX = (800, 800, 900, 900)


@dataclass
class Context:
    service: StudentMonitoringService
    classrooms: ClassroomService
    classroom_repository: MongoClassroomRepository
    state_repository: MongoStudentStateRepository
    detection_repository: MongoDetectionEventRepository
    stream_repository: MongoVideoStreamRepository
    broadcaster: InMemoryBroadcaster


def _rectangle(left: float, top: float, right: float, bottom: float) -> tuple[Point, ...]:
    return (Point(left, top), Point(right, top), Point(right, bottom), Point(left, bottom))


def _student(student_id: str, number: str, name: str) -> Student:
    return Student(
        id=student_id,
        student_number=number,
        name=name,
        birth_date=date(2006, 3, 1),
        classroom_name="A101",
        phone=None,
        guardian_phone="010-0000-0000",
        face_enrollment_id=None,
        face_registered=False,
        is_active=True,
        created_at=NOW,
        updated_at=NOW,
    )


def _person(
    detection_id: str,
    bbox: tuple[int, int, int, int],
    *,
    student_id: str | None = None,
    confidence: float = 0.95,
    identity_confidence: float | None = None,
    track_id: str | None = None,
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
        track_id=track_id,
    )


def _event(
    event_id: str,
    detections: tuple[Detection, ...],
    *,
    captured_at: datetime = NOW,
    camera_id: str = CAMERA_ID,
) -> DetectionEvent:
    return DetectionEvent(
        event_id=event_id,
        camera_id=camera_id,
        stream_id=STREAM_ID,
        classroom_id=CLASSROOM_ID,
        captured_at=captured_at,
        sequence=1,
        frame=FRAME,
        detections=detections,
        received_at=captured_at,
        schema_version=1,
    )


@pytest.fixture
def context(
    mongo_database: MongoDatabase,
    mongo_supports_transactions: bool,
) -> Context:
    if not mongo_supports_transactions:
        pytest.skip("좌석 mutation UoW는 replica set을 요구한다.")

    # 앱이 startup에서 부르는 것과 같은 초기화를 거친다. 새로 추가한
    # MongoStudentStateRepository.ensure_indexes가 실제로 도는지도 여기서 확인된다.
    initialize_indexes(
        mongo_database,
        [
            MongoClassroomRepository.ensure_indexes,
            MongoSeatMutationUnitOfWork.ensure_indexes,
            MongoDetectionEventRepository.ensure_indexes,
            MongoVideoSegmentRepository.ensure_indexes,
            MongoStudentStateRepository.ensure_indexes,
            MongoVideoStreamRepository.ensure_indexes,
            MongoStudentRepository.ensure_indexes,
            MongoRoiConnectionRepository.ensure_indexes,
        ],
    )

    students = MongoStudentRepository(mongo_database)
    students.create(_student(STUDENT_1, "20260001", "통합 학생 A"))
    students.create(_student(STUDENT_2, "20260002", "통합 학생 B"))

    classroom_repository = MongoClassroomRepository(mongo_database)
    uow = MongoSeatMutationUnitOfWork(classroom_repository, mongo_database)
    classrooms = ClassroomService(
        classroom_repository,
        student_lookup=students,
        assignment_repository=MongoSeatAssignmentRepository(mongo_database),
        uow=uow,
        occupancy_confidence_threshold=0.6,
        clock=lambda: NOW,
    )
    classrooms.seed_classroom(
        CreateClassroomCommand(
            id=CLASSROOM_ID, code="IT101", name="통합 강의실", location="A동 1층"
        )
    )
    for index, (seat_id, x) in enumerate((("it-seat-1", 0.1), ("it-seat-2", 0.5)), start=1):
        classrooms.seed_seat(
            CreateSeatCommand(
                id=seat_id,
                classroom_id=CLASSROOM_ID,
                code=f"S0{index}",
                label=f"좌석 S0{index}",
                geometry=SeatGeometry(x=x, y=0.1, width=0.2, height=0.2),
            )
        )
    classrooms.assign_student("it-seat-1", STUDENT_1)
    classrooms.assign_student("it-seat-2", STUDENT_2)

    streams = MongoVideoStreamRepository(mongo_database)
    streams.save(
        VideoStream(
            id=STREAM_ID,
            camera_id=CAMERA_ID,
            classroom_id=CLASSROOM_ID,
            camera_label="통합 카메라",
            playback_kind=PlaybackKind.WEBRTC,
            playback_path=f"/webrtc/{CAMERA_ID}",
            enabled=True,
            last_frame_at=None,
            last_detection_at=None,
            is_demo=False,
            created_at=NOW,
            updated_at=NOW,
        )
    )

    roi_repository = MongoRoiConnectionRepository(mongo_database)
    for seat_id, left, right in (("it-seat-1", 0.1, 0.3), ("it-seat-2", 0.5, 0.7)):
        roi_repository.save(
            RoiConnection(
                classroom_id=CLASSROOM_ID,
                camera_id=CAMERA_ID,
                seat_id=seat_id,
                student_id=None,
                polygon=_rectangle(left, 0.1, right, 0.3),
                # 기준 이미지를 붙이지 않았으므로 0이다. 0이 아니면 `needs_review`로
                # 떨어져 판정에서 빠진다 — 다른 화각의 좌표일 수 있기 때문이다.
                reference_image_revision=0,
                updated_at=NOW,
            )
        )
    roi_service = RoiConnectionService(
        classrooms,
        students,
        roi_repository,
        streams,
        UnavailableCameraFrameGrabber(),
        max_upload_bytes=1024,
        page_size_max=200,
        clock=lambda: NOW,
    )

    detections = MongoDetectionEventRepository(mongo_database)
    state_repository = MongoStudentStateRepository(mongo_database)
    broadcaster = InMemoryBroadcaster()
    service = StudentMonitoringService(
        detection_repository=detections,
        segment_repository=MongoVideoSegmentRepository(mongo_database),
        stream_repository=streams,
        state_repository=state_repository,
        broadcaster=broadcaster,
        classroom_service=classrooms,
        roi_service=roi_service,
        occupancy_confidence_threshold=0.6,
        occupancy_hold_seconds=0,
        identity_confidence_threshold=0.5,
        identity_hold_seconds=0,
        absent_grace_seconds=300,
        stale_seconds=600,
        history_limit=50,
        clock=lambda: NOW,
        student_lookup=students,
    )
    return Context(
        service=service,
        classrooms=classrooms,
        classroom_repository=classroom_repository,
        state_repository=state_repository,
        detection_repository=detections,
        stream_repository=streams,
        broadcaster=broadcaster,
    )


def _seat_state(context: Context, seat_id: str) -> SeatOccupancy:
    seat = context.classroom_repository.get_seat(seat_id)
    assert seat is not None
    return seat.current_occupancy.state


def _state(context: Context, student_id: str) -> StudentSeatState:
    states = {s.student_id: s for s in context.service.list_student_states(CLASSROOM_ID)}
    return states[student_id]


# ============================================================
# 좌석 점유
# ============================================================


def test_탐지가_좌석_점유로_반영된다(context: Context) -> None:
    context.service.receive_inference_event(
        _event("it-e1", (_person("d1", SEAT_1_BBOX),), captured_at=NOW)
    )

    assert _seat_state(context, "it-seat-1") == SeatOccupancy.OCCUPIED
    # 같은 카메라가 보는 자리인데 아무도 없다 = VACANT (UNKNOWN이 아니다).
    assert _seat_state(context, "it-seat-2") == SeatOccupancy.VACANT


def test_탐지가_0건인_프레임도_좌석을_비어_있음으로_기록한다(context: Context) -> None:
    """좌석이 마지막 점유 상태로 얼어붙지 않는지 실제 저장소에서 확인한다."""
    context.service.receive_inference_event(
        _event("it-e1", (_person("d1", SEAT_1_BBOX),), captured_at=NOW)
    )
    assert _seat_state(context, "it-seat-1") == SeatOccupancy.OCCUPIED

    context.service.receive_inference_event(
        _event("it-e2", (), captured_at=NOW + timedelta(seconds=2))
    )

    assert _seat_state(context, "it-seat-1") == SeatOccupancy.VACANT


def test_임계값_미만_탐지만_있는_좌석은_UNKNOWN이다(context: Context) -> None:
    context.service.receive_inference_event(
        _event("it-e1", (_person("d1", SEAT_1_BBOX, confidence=0.3),), captured_at=NOW)
    )

    assert _seat_state(context, "it-seat-1") == SeatOccupancy.UNKNOWN
    assert _seat_state(context, "it-seat-2") == SeatOccupancy.VACANT


# ============================================================
# 학생 상태
# ============================================================


def test_지정_좌석에서_식별되면_재석으로_저장된다(context: Context) -> None:
    context.service.receive_inference_event(
        _event(
            "it-e1",
            (_person("d1", SEAT_1_BBOX, student_id=STUDENT_1, identity_confidence=0.9),),
            captured_at=NOW,
        )
    )

    state = _state(context, STUDENT_1)
    assert state.current_state == StudentState.PRESENT
    assert state.reason == StudentStateReason.IDENTIFIED_AT_ASSIGNED_SEAT
    assert state.current_seat_id == "it-seat-1"
    assert state.current_seat_label == "좌석 S01"
    assert state.confidence == 0.9


def test_다른_좌석에서_식별되면_잘못된_자리로_저장된다(context: Context) -> None:
    context.service.receive_inference_event(
        _event(
            "it-e1",
            (_person("d1", SEAT_2_BBOX, student_id=STUDENT_1, identity_confidence=0.9),),
            captured_at=NOW,
        )
    )

    state = _state(context, STUDENT_1)
    assert state.current_state == StudentState.WRONG_SEAT
    assert state.current_seat_id == "it-seat-2"


def test_좌석_밖에서_식별되면_강의실_안이다(context: Context) -> None:
    context.service.receive_inference_event(
        _event(
            "it-e1",
            (_person("d1", OUTSIDE_BBOX, student_id=STUDENT_1, identity_confidence=0.9),),
            captured_at=NOW,
        )
    )

    state = _state(context, STUDENT_1)
    assert state.current_state == StudentState.IN_CLASSROOM
    assert state.current_seat_id is None


def test_지정_좌석에_모르는_사람이_있으면_재석으로_보지_않는다(context: Context) -> None:
    context.service.receive_inference_event(
        _event("it-e1", (_person("d1", SEAT_1_BBOX),), captured_at=NOW)
    )

    state = _state(context, STUDENT_1)
    assert state.current_state == StudentState.UNKNOWN
    assert state.reason == StudentStateReason.SEAT_OCCUPIED_BY_UNKNOWN


def test_빈_좌석을_유예_시간_내내_보면_결석이_된다(context: Context) -> None:
    """`ABSENT`가 실제 저장소를 거쳐도 유예 시간대로 나오는지 확인한다."""
    for index, seconds in enumerate((0, 100, 299), start=1):
        context.service.receive_inference_event(
            _event(f"it-grace-{index}", (), captured_at=NOW + timedelta(seconds=seconds))
        )
        assert _state(context, STUDENT_1).current_state == StudentState.UNKNOWN

    context.service.receive_inference_event(
        _event("it-grace-final", (), captured_at=NOW + timedelta(seconds=300))
    )

    state = _state(context, STUDENT_1)
    assert state.current_state == StudentState.ABSENT
    assert state.reason == StudentStateReason.SEAT_VACANT_BEYOND_GRACE


def test_상태_전이가_이력으로_남는다(context: Context) -> None:
    context.service.receive_inference_event(
        _event(
            "it-e1",
            (_person("d1", SEAT_1_BBOX, student_id=STUDENT_1, identity_confidence=0.9),),
            captured_at=NOW,
        )
    )
    context.service.receive_inference_event(
        _event(
            "it-e2",
            (_person("d2", SEAT_2_BBOX, student_id=STUDENT_1, identity_confidence=0.9),),
            captured_at=NOW + timedelta(seconds=30),
        )
    )

    history = context.service.list_student_state_history(CLASSROOM_ID, STUDENT_1)

    assert [(item.from_state, item.to_state) for item in history] == [
        (StudentState.PRESENT, StudentState.WRONG_SEAT),
        (StudentState.UNKNOWN, StudentState.PRESENT),
    ]
    assert history[0].event_id == "it-e2"
    assert history[0].reason == StudentStateReason.IDENTIFIED_AT_OTHER_SEAT


def test_같은_이벤트_재수신은_이력을_두_번_만들지_않는다(context: Context) -> None:
    """`_id`가 event 기반이라 중복 insert가 DuplicateKeyError로 막히는지 확인한다."""
    event = _event(
        "it-idem",
        (_person("d1", SEAT_1_BBOX, student_id=STUDENT_1, identity_confidence=0.9),),
        captured_at=NOW,
    )

    first = context.service.receive_inference_event(event)
    second = context.service.receive_inference_event(event)

    assert first.is_new is True
    assert second.is_new is False
    assert len(context.service.list_student_state_history(CLASSROOM_ID, STUDENT_1)) == 1


def test_상태가_그대로면_이력이_쌓이지_않는다(context: Context) -> None:
    for index in range(3):
        context.service.receive_inference_event(
            _event(
                f"it-same-{index}",
                (_person(f"d{index}", SEAT_1_BBOX, student_id=STUDENT_1, identity_confidence=0.9),),
                captured_at=NOW + timedelta(seconds=index),
            )
        )

    history = context.service.list_student_state_history(CLASSROOM_ID, STUDENT_1)
    assert len(history) == 1
    assert history[0].to_state == StudentState.PRESENT


def test_저장된_상태는_서비스를_다시_조립해도_남는다(
    context: Context, mongo_database: MongoDatabase
) -> None:
    """판정 결과가 프로세스 밖에 남는지 — 이 검증은 메모리 저장소로는 할 수 없다."""
    context.service.receive_inference_event(
        _event(
            "it-e1",
            (_person("d1", SEAT_1_BBOX, student_id=STUDENT_1, identity_confidence=0.9),),
            captured_at=NOW,
        )
    )

    reopened = MongoStudentStateRepository(mongo_database)
    records = {r.student_id: r for r in reopened.list_by_classroom(CLASSROOM_ID)}

    assert records[STUDENT_1].state == StudentState.PRESENT
    assert records[STUDENT_1].reason == StudentStateReason.IDENTIFIED_AT_ASSIGNED_SEAT
    assert records[STUDENT_1].seat_id == "it-seat-1"
    assert records[STUDENT_1].observed_at == NOW
    assert records[STUDENT_1].identified_at == NOW


def test_상태는_학생당_하나로_덮어쓴다(context: Context, mongo_database: MongoDatabase) -> None:
    for index, bbox in enumerate((SEAT_1_BBOX, SEAT_2_BBOX, SEAT_1_BBOX), start=1):
        context.service.receive_inference_event(
            _event(
                f"it-upsert-{index}",
                (_person(f"d{index}", bbox, student_id=STUDENT_1, identity_confidence=0.9),),
                captured_at=NOW + timedelta(seconds=index),
            )
        )

    documents = list(mongo_database["student_states"].find({"student_id": STUDENT_1}))
    assert len(documents) == 1
    assert documents[0]["state"] == StudentState.PRESENT.value


def test_늦게_도착한_이벤트가_최신_판정을_되돌리지_않는다(context: Context) -> None:
    context.service.receive_inference_event(
        _event(
            "it-new",
            (_person("d1", SEAT_2_BBOX, student_id=STUDENT_1, identity_confidence=0.9),),
            captured_at=NOW + timedelta(seconds=30),
        )
    )
    context.service.receive_inference_event(
        _event(
            "it-late",
            (_person("d2", SEAT_1_BBOX, student_id=STUDENT_1, identity_confidence=0.9),),
            captured_at=NOW,
        )
    )

    state = _state(context, STUDENT_1)
    assert state.current_state == StudentState.WRONG_SEAT
    assert state.last_observed_at == NOW + timedelta(seconds=30)


# ============================================================
# 새 필드의 저장·복원
# ============================================================


def test_track_id가_저장되고_그대로_돌아온다(context: Context) -> None:
    """새로 더한 필드가 Mongo 문서에 실제로 실리는지 — 대역으로는 못 잡는 실수다."""
    context.service.receive_inference_event(
        _event(
            "it-track",
            (_person("d1", SEAT_1_BBOX, track_id="it-camera-a-17"),),
            captured_at=NOW,
        )
    )

    saved = context.detection_repository.find_by_event_id("it-track")
    assert saved is not None
    assert saved.detections[0].track_id == "it-camera-a-17"


def test_카메라_역할이_저장되고_그대로_돌아온다(context: Context) -> None:
    stream = context.stream_repository.find_by_camera_id(CAMERA_ID)
    assert stream is not None
    assert stream.role is CameraRole.SEAT_JUDGING

    context.stream_repository.save(replace(stream, role=CameraRole.IDENTITY_ONLY))

    reloaded = context.stream_repository.find_by_camera_id(CAMERA_ID)
    assert reloaded is not None
    assert reloaded.role is CameraRole.IDENTITY_ONLY


def test_역할_필드가_없는_기존_문서는_좌석_판정_카메라로_읽는다(
    context: Context, mongo_database: MongoDatabase
) -> None:
    """이번 변경 전에 저장된 문서가 그대로 읽히는지 확인한다."""
    mongo_database["video_streams"].update_one({"_id": STREAM_ID}, {"$unset": {"role": ""}})

    stream = context.stream_repository.find_by_camera_id(CAMERA_ID)
    assert stream is not None
    assert stream.role is CameraRole.SEAT_JUDGING


def test_신원_전용_카메라는_좌석_판정에_참여하지_않는다(
    context: Context, mongo_database: MongoDatabase
) -> None:
    stream = context.stream_repository.find_by_camera_id(CAMERA_ID)
    assert stream is not None
    context.stream_repository.save(replace(stream, role=CameraRole.IDENTITY_ONLY))

    result = context.service.receive_inference_event(
        _event(
            "it-identity-only",
            (_person("d1", SEAT_1_BBOX, student_id=STUDENT_1, identity_confidence=0.9),),
            captured_at=NOW,
        )
    )

    assert result.is_new is True
    assert _seat_state(context, "it-seat-1") == SeatOccupancy.UNKNOWN
    assert mongo_database["student_states"].count_documents({}) == 0
    assert _state(context, STUDENT_1).current_state == StudentState.UNKNOWN


def test_상태_전이_이력_index가_만들어져_있다(
    context: Context, mongo_database: MongoDatabase
) -> None:
    """조회 패턴에 맞는 index가 실제로 생성되는지 확인한다.

    `context` fixture가 앱 startup과 같은 초기화를 돌린다.
    """
    names = set(mongo_database["student_state_history"].index_information())

    assert "student_state_history_classroom_student_time" in names
