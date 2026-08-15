"""탐지 결과 수신 시 좌석 매핑·관측 batch 자동 생성 테스트."""

from __future__ import annotations

from datetime import UTC, datetime
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
from app.shared.broadcaster import InMemoryBroadcaster
from app.student_monitoring.adapters.memory_repository import (
    MemoryDetectionEventRepository,
    MemoryVideoSegmentRepository,
)
from app.student_monitoring.models import Detection, DetectionEvent, FrameInfo
from app.student_monitoring.service import StudentMonitoringService
from app.video_monitoring.adapters.memory_repository import MemoryVideoStreamRepository
from app.video_monitoring.models import PlaybackKind, VideoStream

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


def _make_service(
    classroom_service: ClassroomService,
    *,
    stream_classroom_id: str = _CLASSROOM_ID,
) -> tuple[StudentMonitoringService, MemoryDetectionEventRepository, ClassroomService]:
    detection_repo = MemoryDetectionEventRepository()
    segment_repo = MemoryVideoSegmentRepository()
    stream_repo = _stream_repository(stream_classroom_id)
    service = StudentMonitoringService(
        detection_repository=detection_repo,
        segment_repository=segment_repo,
        stream_repository=stream_repo,
        broadcaster=InMemoryBroadcaster(),
        classroom_service=classroom_service,
        occupancy_confidence_threshold=0.5,
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
        service, _, _ = _make_service(classroom_service)
        # 좌석 1 영역 [0.1, 0.3]x[0.1, 0.3] 안 중심 (200, 200)
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
        # 탐지가 없는 좌석은 UNKNOWN(확인 필요)으로 둔다.
        assert seat_2.current_occupancy.state == SeatOccupancy.UNKNOWN

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
        service, _, _ = _make_service(classroom_service)
        event = _event("event-dup", (_person("det-1", (150, 150, 250, 250)),))

        first = service.receive_inference_event(event)
        second = service.receive_inference_event(event)

        assert first.is_new is True
        assert second.is_new is False
        batch = classroom_service._repository.get_observation_batch("event-dup")
        assert batch is not None
        assert batch.status == ObservationBatchStatus.COMPLETED

    def test_receive_with_seat_without_geometry_excludes_seat(self) -> None:
        """geometry가 없는 좌석은 매핑에서 제외된다."""
        classroom_service = _build_classroom_service(
            (
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
                    geometry=None,
                ),
            )
        )
        service, _, _ = _make_service(classroom_service)

        service.receive_inference_event(
            _event("event-geom", (_person("det-1", (150, 150, 250, 250)),))
        )

        batch = classroom_service._repository.get_observation_batch("event-geom")
        assert batch is not None
        assert len(batch.observations) == 1
        assert batch.observations[0].seat_id == "seat-1"
        # geometry가 없는 좌석의 상태는 바뀌지 않는다.
        seat_2 = classroom_service._repository.get_seat("seat-2")
        assert seat_2 is not None
        assert seat_2.current_occupancy.state == SeatOccupancy.UNKNOWN

    def test_receive_with_low_confidence_detection_keeps_seat_unknown(self) -> None:
        """신뢰도가 임계값 미만이면 좌석을 점유로 바꾸지 않는다."""
        classroom_service = _build_classroom_service(_two_seats())
        service, _, _ = _make_service(classroom_service)
        event = _event(
            "event-low",
            (_person("det-1", (150, 150, 250, 250), confidence=0.3),),
        )

        service.receive_inference_event(event)

        seat_1 = classroom_service._repository.get_seat("seat-1")
        assert seat_1 is not None
        assert seat_1.current_occupancy.state == SeatOccupancy.UNKNOWN

    def test_mapping_failure_does_not_block_event_save(self) -> None:
        """좌석 목록 조회가 예기치 못한 예외로 실패해도 탐지 이벤트는 정상 저장된다."""
        classroom_service = _build_classroom_service(_two_seats())
        service, detection_repo, _ = _make_service(classroom_service)
        event = _event("event-map-fail", (_person("det-1", (150, 150, 250, 250)),))

        with patch.object(
            classroom_service, "list_all_seats", side_effect=RuntimeError("저장소 오류")
        ):
            result = service.receive_inference_event(event)

        assert result.is_new is True
        assert detection_repo.find_by_event_id("event-map-fail") is not None
        # 매핑이 실패했으므로 관측 batch는 만들어지지 않는다.
        assert classroom_service._repository.get_observation_batch("event-map-fail") is None

    def test_observation_record_failure_does_not_block_event_save(self) -> None:
        """관측 batch 기록이 실패해도 탐지 이벤트는 정상 저장된다."""
        classroom_service = _build_classroom_service(_two_seats())
        service, detection_repo, _ = _make_service(classroom_service)
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
        detection_repo = MemoryDetectionEventRepository()
        segment_repo = MemoryVideoSegmentRepository()
        stream_repo = _stream_repository()
        broadcaster = InMemoryBroadcaster()
        queue = broadcaster.subscribe()
        service = StudentMonitoringService(
            detection_repository=detection_repo,
            segment_repository=segment_repo,
            stream_repository=stream_repo,
            broadcaster=broadcaster,
            classroom_service=classroom_service,
            occupancy_confidence_threshold=0.5,
        )
        # 좌석 1 영역 [0.1, 0.3]x[0.1, 0.3] 안 중심 (200, 200)
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

        # 탐지가 없는 좌석은 UNKNOWN 상태로 발행된다.
        unknown = next(item for item in occupancy_events if item["seat_id"] == "seat-2")
        assert unknown["state"] == SeatOccupancy.UNKNOWN.value
        assert unknown["confidence"] == 0.0

    def test_receive_duplicate_event_publishes_no_occupancy_events(self) -> None:
        """같은 event_id 재수신에서는 occupancy SSE 이벤트를 다시 발행하지 않는다."""
        classroom_service = _build_classroom_service(_two_seats())
        detection_repo = MemoryDetectionEventRepository()
        segment_repo = MemoryVideoSegmentRepository()
        stream_repo = _stream_repository()
        broadcaster = InMemoryBroadcaster()
        queue = broadcaster.subscribe()
        service = StudentMonitoringService(
            detection_repository=detection_repo,
            segment_repository=segment_repo,
            stream_repository=stream_repo,
            broadcaster=broadcaster,
            classroom_service=classroom_service,
            occupancy_confidence_threshold=0.5,
        )
        event = _event("event-occ-dup", (_person("det-1", (150, 150, 250, 250)),))

        first = service.receive_inference_event(event)
        second = service.receive_inference_event(event)

        assert first.is_new is True
        assert second.is_new is False
        # 첫 수신의 탐지·점유 이벤트 이후에 새 이벤트가 없어야 한다.
        queue.get_nowait()
        queue.get_nowait()
        queue.get_nowait()
        assert queue.empty()
