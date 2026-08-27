"""탐지 결과 수신 시 좌석 매핑·관측 batch 자동 생성 테스트."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from app.classrooms.adapters.memory_repository import InMemoryClassroomRepository
from app.classrooms.models import (
    CreateClassroomCommand,
    CreateSeatCommand,
    ObservationBatchStatus,
    OccupancySource,
    SeatGeometry,
    SeatOccupancy,
)
from app.classrooms.service import ClassroomService
from app.roi_connections.adapters.ffmpeg_camera import UnavailableCameraFrameGrabber
from app.roi_connections.adapters.memory import InMemoryRoiConnectionRepository
from app.roi_connections.models import Point, RoiConnection
from app.roi_connections.service import RoiConnectionService
from app.shared.adapters.memory_student_lookup import InMemoryStudentLookup
from app.shared.broadcaster import InMemoryBroadcaster
from app.student_monitoring.adapters.memory_repository import (
    MemoryDetectionEventRepository,
    MemoryStudentStateRepository,
    MemoryVideoSegmentRepository,
)
from app.student_monitoring.models import Detection, DetectionEvent, FrameInfo
from app.student_monitoring.service import StudentMonitoringService
from app.video_monitoring.adapters.memory_repository import MemoryVideoStreamRepository
from app.video_monitoring.models import CameraRole, PlaybackKind, VideoStream

from ..roi_connections.fakes import FakeSeatedDetectionSource

_CLASSROOM_ID = "classroom-a101"


def _build_classroom_service(seats: tuple[CreateSeatCommand, ...]) -> ClassroomService:
    service = ClassroomService(
        InMemoryClassroomRepository(),
        occupancy_confidence_threshold=0.5,
        clock=lambda: datetime(2026, 8, 13, 9, 0, tzinfo=UTC),
    )
    service.seed_classroom(
        CreateClassroomCommand(
            id=_CLASSROOM_ID, code="A101", name="A101 일반 강의실", location="A동 1층"
        )
    )
    for command in seats:
        service.seed_seat(command)
    return service


def _two_seats() -> tuple[CreateSeatCommand, ...]:
    return (
        CreateSeatCommand(
            id="seat-1",
            classroom_id=_CLASSROOM_ID,
            code="S01",
            label="좌석 1",
            geometry=SeatGeometry(x=0.1, y=0.1, width=0.2, height=0.2),
        ),
        CreateSeatCommand(
            id="seat-2",
            classroom_id=_CLASSROOM_ID,
            code="S02",
            label="좌석 2",
            geometry=SeatGeometry(x=0.5, y=0.1, width=0.2, height=0.2),
        ),
    )


def _stream_repository(
    classroom_id: str = _CLASSROOM_ID,
) -> MemoryVideoStreamRepository:
    repository = MemoryVideoStreamRepository()
    repository.save(
        VideoStream(
            id="stream-camera-a",
            camera_id="camera-a",
            classroom_id=classroom_id,
            camera_label="Left Camera",
            playback_kind=PlaybackKind.WEBRTC,
            playback_path="/webrtc/camera-a",
            enabled=True,
            last_frame_at=None,
            last_detection_at=None,
            is_demo=False,
            created_at=datetime(2026, 8, 13, 9, 0, tzinfo=UTC),
            updated_at=datetime(2026, 8, 13, 9, 0, tzinfo=UTC),
        )
    )
    return repository


def _rectangle_roi(
    seat_id: str,
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    camera_id: str = "camera-a",
) -> RoiConnection:
    """좌석 사각형을 카메라 ROI로 등록한다.

    revision 0은 live 영상에서 저장한 ROI라 기준 이미지 없이도 유효하다(결정 0019).
    """
    return RoiConnection(
        classroom_id=_CLASSROOM_ID,
        camera_id=camera_id,
        seat_id=seat_id,
        student_id=None,
        polygon=(
            Point(x=x, y=y),
            Point(x=x + width, y=y),
            Point(x=x + width, y=y + height),
            Point(x=x, y=y + height),
        ),
        reference_image_revision=0,
        updated_at=datetime(2026, 8, 13, 9, 0, tzinfo=UTC),
    )


def _two_seat_rois() -> tuple[RoiConnection, ...]:
    """_two_seats()와 같은 영역을 카메라 ROI로 등록한다."""
    return (
        _rectangle_roi("seat-1", 0.1, 0.1, 0.2, 0.2),
        _rectangle_roi("seat-2", 0.5, 0.1, 0.2, 0.2),
    )


def _make_service(
    classroom_service: ClassroomService,
    *,
    stream_classroom_id: str = _CLASSROOM_ID,
    broadcaster: InMemoryBroadcaster | None = None,
    rois: tuple[RoiConnection, ...] = (),
    hold_seconds: float = 0,
) -> tuple[StudentMonitoringService, MemoryDetectionEventRepository, ClassroomService]:
    detection_repo = MemoryDetectionEventRepository()
    segment_repo = MemoryVideoSegmentRepository()
    stream_repo = _stream_repository(stream_classroom_id)
    student_lookup = InMemoryStudentLookup()
    roi_repository = InMemoryRoiConnectionRepository()
    for roi in rois:
        roi_repository.save(roi)
    roi_service = RoiConnectionService(
        classroom_service,
        student_lookup,
        roi_repository,
        stream_repo,
        UnavailableCameraFrameGrabber(),
        FakeSeatedDetectionSource(),
        max_upload_bytes=1024,
        page_size_max=200,
        clock=lambda: datetime(2026, 8, 13, 9, 0, tzinfo=UTC),
    )
    service = StudentMonitoringService(
        detection_repository=detection_repo,
        segment_repository=segment_repo,
        stream_repository=stream_repo,
        state_repository=MemoryStudentStateRepository(),
        broadcaster=broadcaster or InMemoryBroadcaster(),
        classroom_service=classroom_service,
        roi_service=roi_service,
        occupancy_confidence_threshold=0.5,
        occupancy_hold_seconds=hold_seconds,
        identity_confidence_threshold=0.5,
        stale_seconds=300,
        identity_hold_seconds=0,
        absent_grace_seconds=300,
        history_limit=50,
        clock=lambda: datetime(2026, 8, 13, 9, 0, tzinfo=UTC),
        student_lookup=student_lookup,
    )
    return service, detection_repo, classroom_service


def _person(
    detection_id: str, bbox: tuple[int, int, int, int], *, confidence: float = 0.95
) -> Detection:
    return Detection(
        detection_id=detection_id,
        class_id=0,
        class_name="person",
        confidence=confidence,
        bbox=bbox,
        student_id=None,
        identity_confidence=None,
        face_bbox=None,
    )


def _event(
    event_id: str,
    detections: tuple[Detection, ...],
    *,
    captured_at: datetime | None = None,
    frame: FrameInfo | None = None,
) -> DetectionEvent:
    return DetectionEvent(
        event_id=event_id,
        camera_id="camera-a",
        stream_id="",
        classroom_id="",
        captured_at=captured_at or datetime(2026, 8, 13, 9, 5, 0, tzinfo=UTC),
        sequence=1,
        frame=frame or FrameInfo(width_pixels=1000, height_pixels=1000),
        detections=detections,
        received_at=datetime(2026, 8, 13, 9, 5, 1, tzinfo=UTC),
        schema_version=1,
    )


class TestAutomaticSeatMapping:
    def test_receive_event_creates_observation_batch_and_updates_occupancy(self) -> None:
        """탐지 수신 시 좌석 관측 batch가 생기고 seats.current_occupancy가 갱신된다."""
        classroom_service = _build_classroom_service(_two_seats())
        service, _, _ = _make_service(classroom_service, rois=_two_seat_rois())
        # 좌석 1 ROI [0.1, 0.3]x[0.1, 0.3] 안 중심 (200, 200)
        event = _event("event-1", (_person("det-1", (150, 150, 250, 250)),))

        result = service.receive_inference_event(event)

        assert result.is_new is True
        assert result.event.stream_id == "stream-camera-a"
        assert result.event.classroom_id == _CLASSROOM_ID
        batch = classroom_service._repository.get_observation_batch("event-1")
        assert batch is not None
        assert batch.classroom_id == _CLASSROOM_ID
        assert batch.source == OccupancySource.SYSTEM
        assert batch.status == ObservationBatchStatus.COMPLETED
        assert len(batch.observations) == 2
        assert batch.observations[0].seat_id == "seat-1"
        assert batch.observations[0].occupied is True
        assert batch.observations[0].confidence == 0.95

        seat_1 = classroom_service._repository.get_seat("seat-1")
        seat_2 = classroom_service._repository.get_seat("seat-2")
        assert seat_1 is not None and seat_2 is not None
        assert seat_1.current_occupancy.state == SeatOccupancy.OCCUPIED
        assert seat_1.current_occupancy.source == OccupancySource.SYSTEM
        assert seat_1.current_occupancy.event_id == "event-1"
        # 이 카메라가 보는 좌석인데 아무도 없었다 = VACANT.
        # UNKNOWN은 ROI가 없어 관측 대상이 아니거나 신뢰도가 모자랄 때만 쓴다.
        assert seat_2.current_occupancy.state == SeatOccupancy.VACANT

    def test_receive_without_classroom_skips_mapping_but_saves_event(self) -> None:
        """강의실이 등록되지 않았으면 매핑을 건너뛰고 탐지 이벤트는 정상 저장한다."""
        classroom_service = _build_classroom_service(())
        service, detection_repo, _ = _make_service(classroom_service)
        event = _event("event-noclass", (_person("det-1", (150, 150, 250, 250)),))

        result = service.receive_inference_event(event)

        assert result.is_new is True
        assert detection_repo.find_by_event_id("event-noclass") is not None
        assert classroom_service._repository.get_observation_batch("event-noclass") is None

    def test_broken_stream_classroom_reference_saves_event_and_skips_mapping(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        classroom_service = _build_classroom_service(_two_seats())
        service, detection_repo, _ = _make_service(
            classroom_service,
            stream_classroom_id="classroom-missing",
        )

        with caplog.at_level("WARNING"):
            result = service.receive_inference_event(
                _event("event-broken-ref", (_person("det-1", (150, 150, 250, 250)),))
            )

        assert result.is_new is True
        saved = detection_repo.find_by_event_id("event-broken-ref")
        assert saved is not None
        assert saved.stream_id == "stream-camera-a"
        assert saved.classroom_id == "classroom-missing"
        assert classroom_service._repository.get_observation_batch("event-broken-ref") is None
        assert "event_id=event-broken-ref" in caplog.text
        assert "camera_id=camera-a" in caplog.text
        assert "classroom_id=classroom-missing" in caplog.text

    def test_receive_empty_detections_creates_no_batch(self) -> None:
        """미탐지 프레임은 좌석 상태를 되돌리지 않도록 batch를 만들지 않는다."""
        classroom_service = _build_classroom_service(_two_seats())
        service, _, _ = _make_service(classroom_service)

        result = service.receive_inference_event(_event("event-empty", ()))

        assert result.is_new is True
        assert classroom_service._repository.get_observation_batch("event-empty") is None

    def test_receive_duplicate_event_does_not_repeat_mapping(self) -> None:
        """같은 event_id 재수신에서는 매핑을 반복하지 않는다."""
        classroom_service = _build_classroom_service(_two_seats())
        service, _, _ = _make_service(classroom_service, rois=_two_seat_rois())
        event = _event("event-dup", (_person("det-1", (150, 150, 250, 250)),))

        first = service.receive_inference_event(event)
        second = service.receive_inference_event(event)

        assert first.is_new is True
        assert second.is_new is False
        batch = classroom_service._repository.get_observation_batch("event-dup")
        assert batch is not None
        assert batch.status == ObservationBatchStatus.COMPLETED

    def test_seat_without_roi_for_this_camera_is_not_observed(self) -> None:
        """이 카메라에 ROI가 없는 좌석은 관측 대상이 아니다 (결정 0020).

        강의실을 나눠 보는 구성에서 다른 카메라 담당 좌석까지 "비어 있음"으로
        기록하면 그쪽 관측을 덮어쓰게 된다.
        """
        classroom_service = _build_classroom_service(_two_seats())
        # 좌석 2는 다른 카메라 담당이라 이 카메라에 ROI가 없다.
        service, _, _ = _make_service(
            classroom_service,
            rois=(_rectangle_roi("seat-1", 0.1, 0.1, 0.2, 0.2),),
        )

        service.receive_inference_event(
            _event("event-roi-scope", (_person("det-1", (150, 150, 250, 250)),))
        )

        batch = classroom_service._repository.get_observation_batch("event-roi-scope")
        assert batch is not None
        assert len(batch.observations) == 1
        assert batch.observations[0].seat_id == "seat-1"
        # 관측 대상이 아닌 좌석의 상태는 이 이벤트로 바뀌지 않는다.
        seat_2 = classroom_service._repository.get_seat("seat-2")
        assert seat_2 is not None
        assert seat_2.current_occupancy.state == SeatOccupancy.UNKNOWN
        assert seat_2.current_occupancy.event_id is None

    def test_receive_without_any_roi_creates_no_batch(self) -> None:
        """ROI가 하나도 없으면 좌석을 추정하지 않고 관측을 만들지 않는다."""
        classroom_service = _build_classroom_service(_two_seats())
        service, detection_repo, _ = _make_service(classroom_service)

        result = service.receive_inference_event(
            _event("event-no-roi", (_person("det-1", (150, 150, 250, 250)),))
        )

        assert result.is_new is True
        assert detection_repo.find_by_event_id("event-no-roi") is not None
        assert classroom_service._repository.get_observation_batch("event-no-roi") is None

    def test_receive_with_low_confidence_detection_keeps_seat_unknown(self) -> None:
        """신뢰도가 임계값 미만이면 좌석을 점유로 바꾸지 않는다."""
        classroom_service = _build_classroom_service(_two_seats())
        service, _, _ = _make_service(classroom_service, rois=_two_seat_rois())
        event = _event(
            "event-low",
            (_person("det-1", (150, 150, 250, 250), confidence=0.3),),
        )

        service.receive_inference_event(event)

        seat_1 = classroom_service._repository.get_seat("seat-1")
        assert seat_1 is not None
        assert seat_1.current_occupancy.state == SeatOccupancy.UNKNOWN

    def test_mapping_failure_does_not_block_event_save(self) -> None:
        """ROI 조회가 예기치 못한 예외로 실패해도 탐지 이벤트는 정상 저장된다."""
        classroom_service = _build_classroom_service(_two_seats())
        service, detection_repo, _ = _make_service(classroom_service, rois=_two_seat_rois())
        event = _event("event-map-fail", (_person("det-1", (150, 150, 250, 250)),))

        with patch.object(
            service._roi_service, "list_valid_connections", side_effect=RuntimeError("저장소 오류")
        ):
            result = service.receive_inference_event(event)

        assert result.is_new is True
        assert detection_repo.find_by_event_id("event-map-fail") is not None
        # 매핑이 실패했으므로 관측 batch는 만들어지지 않는다.
        assert classroom_service._repository.get_observation_batch("event-map-fail") is None

    def test_observation_record_failure_does_not_block_event_save(self) -> None:
        """관측 batch 기록이 실패해도 탐지 이벤트는 정상 저장된다."""
        classroom_service = _build_classroom_service(_two_seats())
        service, detection_repo, _ = _make_service(classroom_service, rois=_two_seat_rois())
        event = _event("event-record-fail", (_person("det-1", (150, 150, 250, 250)),))

        with patch.object(
            classroom_service,
            "record_seat_observation_batch",
            side_effect=RuntimeError("저장소 오류"),
        ):
            result = service.receive_inference_event(event)

        assert result.is_new is True
        assert detection_repo.find_by_event_id("event-record-fail") is not None

    def test_receive_event_publishes_occupancy_events(self) -> None:
        """탐지 수신 시 좌석 관측 batch와 함께 occupancy SSE 이벤트를 발행한다."""
        classroom_service = _build_classroom_service(_two_seats())
        broadcaster = InMemoryBroadcaster()
        queue = broadcaster.subscribe()
        service, _, _ = _make_service(
            classroom_service,
            broadcaster=broadcaster,
            rois=_two_seat_rois(),
        )
        # 좌석 1 ROI [0.1, 0.3]x[0.1, 0.3] 안 중심 (200, 200)
        event = _event("event-occ", (_person("det-1", (150, 150, 250, 250)),))

        service.receive_inference_event(event)

        # 탐지 이벤트 1건과 좌석 점유 이벤트 2건이 발행된다.
        first = queue.get_nowait()
        assert first["type"] == "detection"
        occupancy_events = [queue.get_nowait(), queue.get_nowait()]
        assert all(item["type"] == "occupancy" for item in occupancy_events)
        assert queue.empty()

        occupied = next(item for item in occupancy_events if item["seat_id"] == "seat-1")
        assert occupied["classroom_id"] == _CLASSROOM_ID
        assert occupied["event_id"] == "event-occ"
        assert occupied["state"] == SeatOccupancy.OCCUPIED.value
        assert occupied["confidence"] == 0.95

        # 탐지가 없는 좌석은 VACANT 상태로 발행된다.
        vacant = next(item for item in occupancy_events if item["seat_id"] == "seat-2")
        assert vacant["state"] == SeatOccupancy.VACANT.value
        assert vacant["confidence"] == 0.0

    def test_receive_duplicate_event_publishes_no_occupancy_events(self) -> None:
        """같은 event_id 재수신에서는 occupancy SSE 이벤트를 다시 발행하지 않는다."""
        classroom_service = _build_classroom_service(_two_seats())
        broadcaster = InMemoryBroadcaster()
        queue = broadcaster.subscribe()
        service, _, _ = _make_service(
            classroom_service,
            broadcaster=broadcaster,
            rois=_two_seat_rois(),
        )
        event = _event("event-occ-dup", (_person("det-1", (150, 150, 250, 250)),))

        first = service.receive_inference_event(event)
        second = service.receive_inference_event(event)

        assert first.is_new is True
        assert second.is_new is False

        published = []
        while not queue.empty():
            published.append(queue.get_nowait())
        kinds = [item.get("type") for item in published]
        # **점유 이벤트는 재수신에서 다시 나가지 않는다.** 좌석 상태를 두 번 바꾸면
        # 안 되기 때문이다. 첫 수신의 좌석 2개분만 있어야 한다.
        assert kinds.count("occupancy") == 2
        # 반면 bbox overlay는 두 번 나간다. 저장 여부를 확인하기 전에 내보내기
        # 때문이며(오버레이는 저장소가 필요 없다), 같은 상자를 덮어 그리므로
        # 화면 결과는 같다.
        assert kinds.count("detection") == 2


def _seat_state(classroom_service: ClassroomService, seat_id: str) -> SeatOccupancy:
    seat = classroom_service._repository.get_seat(seat_id)
    assert seat is not None
    return seat.current_occupancy.state


def test_점유가_한_프레임_끊겨도_유지_시간_안이면_점유로_남는다() -> None:
    """앉은 사람도 프레임마다 잡히지는 않는다. 그 틈에 좌석이 깜빡이면 안 된다."""
    classroom_service = _build_classroom_service(_two_seats())
    service, _, classrooms = _make_service(classroom_service, rois=_two_seat_rois(), hold_seconds=5)
    base = datetime(2026, 8, 13, 9, 5, 0, tzinfo=UTC)

    service.receive_inference_event(
        _event("e1", (_person("d1", (150, 150, 250, 250)),), captured_at=base)
    )
    assert _seat_state(classrooms, "seat-1") == SeatOccupancy.OCCUPIED

    # 다음 프레임에서 아무도 잡히지 않았다.
    service.receive_inference_event(
        _event(
            "e2", (_person("d2", (900, 900, 950, 950)),), captured_at=base + timedelta(seconds=2)
        )
    )

    assert _seat_state(classrooms, "seat-1") == SeatOccupancy.OCCUPIED


def test_유지_시간이_지나면_점유를_놓는다() -> None:
    """자리를 뜬 사람을 계속 앉아 있다고 기록하지 않는다.

    놓은 뒤는 VACANT다. 그 카메라의 ROI에 등록된 좌석이므로 "보고 있는데 아무도 없다"가
    관측 결과이고, 그것을 UNKNOWN으로 뭉개면 화면에서 빈 자리와 못 본 자리가 섞인다.
    """
    classroom_service = _build_classroom_service(_two_seats())
    service, _, classrooms = _make_service(classroom_service, rois=_two_seat_rois(), hold_seconds=5)
    base = datetime(2026, 8, 13, 9, 5, 0, tzinfo=UTC)

    service.receive_inference_event(
        _event("e1", (_person("d1", (150, 150, 250, 250)),), captured_at=base)
    )
    service.receive_inference_event(
        _event(
            "e2", (_person("d2", (900, 900, 950, 950)),), captured_at=base + timedelta(seconds=9)
        )
    )

    assert _seat_state(classrooms, "seat-1") == SeatOccupancy.VACANT


def test_붙들린_좌석은_유지_구간을_다시_늘리지_못한다() -> None:
    """한 번 잡힌 좌석이 영영 점유로 남지 않아야 한다.

    붙들려서 점유가 된 관측까지 "방금 봤다"로 기록하면 유지 시간이 계속 갱신된다.
    """
    classroom_service = _build_classroom_service(_two_seats())
    service, _, classrooms = _make_service(classroom_service, rois=_two_seat_rois(), hold_seconds=5)
    base = datetime(2026, 8, 13, 9, 5, 0, tzinfo=UTC)
    elsewhere = (_person("dx", (900, 900, 950, 950)),)

    service.receive_inference_event(
        _event("e1", (_person("d1", (150, 150, 250, 250)),), captured_at=base)
    )
    # 3초 간격으로 계속 비어 있는 관측이 온다. 매번 유지 시간이 갱신되면 영원히 점유다.
    for i, seconds in enumerate((3, 6, 9), start=2):
        service.receive_inference_event(
            _event(f"e{i}", elsewhere, captured_at=base + timedelta(seconds=seconds))
        )

    assert _seat_state(classrooms, "seat-1") == SeatOccupancy.VACANT


def test_유지_시간이_0이면_이전과_같이_곧바로_놓는다() -> None:
    classroom_service = _build_classroom_service(_two_seats())
    service, _, classrooms = _make_service(classroom_service, rois=_two_seat_rois(), hold_seconds=0)
    base = datetime(2026, 8, 13, 9, 5, 0, tzinfo=UTC)

    service.receive_inference_event(
        _event("e1", (_person("d1", (150, 150, 250, 250)),), captured_at=base)
    )
    service.receive_inference_event(
        _event(
            "e2", (_person("d2", (900, 900, 950, 950)),), captured_at=base + timedelta(seconds=1)
        )
    )

    assert _seat_state(classrooms, "seat-1") == SeatOccupancy.VACANT


def test_탐지가_0건인_이벤트도_좌석을_비어_있음으로_관측한다() -> None:
    """마지막 사람이 나간 뒤 좌석이 점유인 채로 얼어붙지 않아야 한다.

    worker는 사람이 잡히지 않은 프레임에도 이벤트를 보낸다. 예전에는 `detections`가
    비면 좌석 매핑을 통째로 건너뛰어, 아무도 없는 강의실의 좌석이 마지막 `OCCUPIED`로
    영원히 남았다. 유지 시간(hold)도 다음 탐지가 올 때까지 만료되지 않았다.
    """
    classroom_service = _build_classroom_service(_two_seats())
    service, _, classrooms = _make_service(classroom_service, rois=_two_seat_rois(), hold_seconds=5)
    base = datetime(2026, 8, 13, 9, 5, 0, tzinfo=UTC)

    service.receive_inference_event(
        _event("e1", (_person("d1", (150, 150, 250, 250)),), captured_at=base)
    )
    assert _seat_state(classrooms, "seat-1") == SeatOccupancy.OCCUPIED

    # 아무도 잡히지 않은 프레임이 이어진다. 유지 시간 안에는 점유를 붙들어 둔다.
    service.receive_inference_event(_event("e2", (), captured_at=base + timedelta(seconds=2)))
    assert _seat_state(classrooms, "seat-1") == SeatOccupancy.OCCUPIED

    # 유지 시간이 지나면 놓는다.
    service.receive_inference_event(_event("e3", (), captured_at=base + timedelta(seconds=9)))
    assert _seat_state(classrooms, "seat-1") == SeatOccupancy.VACANT


def test_임계값_미만_탐지만_있는_좌석은_UNKNOWN이다() -> None:
    """흐릿하게 잡힌 자리를 비었다고 단정하지 않는다."""
    classroom_service = _build_classroom_service(_two_seats())
    service, _, classrooms = _make_service(classroom_service, rois=_two_seat_rois())
    base = datetime(2026, 8, 13, 9, 5, 0, tzinfo=UTC)

    # _build_classroom_service의 임계값은 0.5다.
    service.receive_inference_event(
        _event(
            "e1",
            (_person("d1", (150, 150, 250, 250), confidence=0.3),),
            captured_at=base,
        )
    )

    assert _seat_state(classrooms, "seat-1") == SeatOccupancy.UNKNOWN
    # 탐지가 아예 없던 좌석과 구분된다.
    assert _seat_state(classrooms, "seat-2") == SeatOccupancy.VACANT


def test_임계값_미만_탐지는_유지_시간을_늘리지_못한다() -> None:
    """약한 근거가 "방금 확실히 봤다"로 둔갑해 점유를 연장하면 안 된다."""
    classroom_service = _build_classroom_service(_two_seats())
    service, _, classrooms = _make_service(classroom_service, rois=_two_seat_rois(), hold_seconds=5)
    base = datetime(2026, 8, 13, 9, 5, 0, tzinfo=UTC)

    service.receive_inference_event(
        _event("e1", (_person("d1", (150, 150, 250, 250)),), captured_at=base)
    )
    # 3초 간격으로 임계값 미만 탐지만 들어온다. 이것이 유지 시간을 갱신하면 영원히 점유다.
    for index, seconds in enumerate((3, 6, 9), start=2):
        service.receive_inference_event(
            _event(
                f"e{index}",
                (_person(f"d{index}", (150, 150, 250, 250), confidence=0.3),),
                captured_at=base + timedelta(seconds=seconds),
            )
        )

    assert _seat_state(classrooms, "seat-1") == SeatOccupancy.UNKNOWN


def _identity_only_service(
    classroom_service: ClassroomService,
    rois: tuple[RoiConnection, ...],
) -> tuple[StudentMonitoringService, ClassroomService]:
    """입구 카메라(신원 전용)로 등록된 스트림을 가진 서비스를 만든다."""
    service, _, classrooms = _make_service(classroom_service, rois=rois)
    stream = service._stream_repository.find_by_camera_id("camera-a")
    assert stream is not None
    service._stream_repository.save(replace(stream, role=CameraRole.IDENTITY_ONLY))
    return service, classrooms


def test_신원_전용_카메라는_좌석_판정에_참여하지_않는다() -> None:
    """입구 카메라의 이벤트가 조망 카메라의 좌석 판정을 덮지 않는다(결정 0024의 3번).

    좌석을 담지 않는 화각의 이벤트가 "최신"이라는 이유로 직전 판정을 UNKNOWN으로
    되돌리는 것이 결정 0020이 남은 일로 적어 둔 문제였다.
    """
    classroom_service = _build_classroom_service(_two_seats())
    service, classrooms = _identity_only_service(classroom_service, _two_seat_rois())
    base = datetime(2026, 8, 13, 9, 5, 0, tzinfo=UTC)

    result = service.receive_inference_event(
        _event("e1", (_person("d1", (150, 150, 250, 250)),), captured_at=base)
    )

    # 이벤트 자체는 정상 저장된다. 좌석 상태만 건드리지 않는다.
    assert result.is_new is True
    assert _seat_state(classrooms, "seat-1") == SeatOccupancy.UNKNOWN
    assert classroom_service._repository.get_observation_batch("e1") is None


def test_track_id는_저장되고_그대로_돌아온다() -> None:
    """트래킹이 붙기 전에도 값을 잃지 않고 실어 나르는지 확인한다."""
    classroom_service = _build_classroom_service(_two_seats())
    service, detection_repo, _ = _make_service(classroom_service, rois=_two_seat_rois())
    detection = replace(_person("d1", (150, 150, 250, 250)), track_id="camera-a-17")

    service.receive_inference_event(_event("e-track", (detection,)))

    saved = detection_repo.find_by_event_id("e-track")
    assert saved is not None
    assert saved.detections[0].track_id == "camera-a-17"


def test_늦게_도착한_프레임이_유지_시간을_되돌리지_않는다() -> None:
    """순서가 뒤바뀌어 도착한 오래된 프레임이 점유 유지 구간을 앞당기면 안 된다."""
    classroom_service = _build_classroom_service(_two_seats())
    service, _, classrooms = _make_service(classroom_service, rois=_two_seat_rois(), hold_seconds=5)
    base = datetime(2026, 8, 13, 9, 5, 0, tzinfo=UTC)
    seated = (_person("d1", (150, 150, 250, 250)),)

    service.receive_inference_event(
        _event("e-new", seated, captured_at=base + timedelta(seconds=4))
    )
    # 4초 시점보다 오래된 프레임이 뒤늦게 도착한다.
    service.receive_inference_event(_event("e-late", seated, captured_at=base))

    # 마지막으로 본 시각은 4초 그대로여야 한다. 되돌아갔다면 6초에 이미 놓았을 것이다.
    service.receive_inference_event(_event("e-empty", (), captured_at=base + timedelta(seconds=6)))
    assert _seat_state(classrooms, "seat-1") == SeatOccupancy.OCCUPIED

    service.receive_inference_event(_event("e-gone", (), captured_at=base + timedelta(seconds=10)))
    assert _seat_state(classrooms, "seat-1") == SeatOccupancy.VACANT
