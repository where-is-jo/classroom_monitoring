"""Router tests."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.classrooms.adapters.memory_repository import InMemoryClassroomRepository
from app.classrooms.models import CreateClassroomCommand
from app.classrooms.service import ClassroomService
from app.main import app
from app.shared.broadcaster import InMemoryBroadcaster
from app.shared.dependencies import get_student_monitoring_service
from app.student_monitoring.adapters.memory_repository import (
    MemoryDetectionEventRepository,
    MemoryVideoSegmentRepository,
)
from app.student_monitoring.service import StudentMonitoringService
from app.video_monitoring.adapters.memory_repository import MemoryVideoStreamRepository
from app.video_monitoring.models import PlaybackKind, VideoStream


def _make_stream() -> VideoStream:
    return VideoStream(
        id="stream-camera-a",
        camera_id="camera-a",
        classroom_id="classroom-a101",
        camera_label="Left Camera",
        playback_kind=PlaybackKind.WEBRTC,
        playback_path="/webrtc/camera-a",
        enabled=True,
        last_frame_at=None,
        last_detection_at=None,
        is_demo=False,
        created_at=datetime(2026, 8, 12, 0, 0, 0, tzinfo=UTC),
        updated_at=datetime(2026, 8, 12, 0, 0, 0, tzinfo=UTC),
    )


def _make_service() -> tuple[StudentMonitoringService, MemoryDetectionEventRepository]:
    detection_repo = MemoryDetectionEventRepository()
    segment_repo = MemoryVideoSegmentRepository()
    stream_repo = MemoryVideoStreamRepository()
    broadcaster = InMemoryBroadcaster()
    classroom_service = ClassroomService(
        InMemoryClassroomRepository(),
        occupancy_confidence_threshold=0.5,
        clock=lambda: datetime(2026, 8, 12, 0, 0, 0, tzinfo=UTC),
    )
    classroom_service.seed_classroom(
        CreateClassroomCommand(
            id="classroom-a101",
            code="A101",
            name="A101 일반 강의실",
            location="A동 1층",
        )
    )
    service = StudentMonitoringService(
        detection_repository=detection_repo,
        segment_repository=segment_repo,
        stream_repository=stream_repo,
        broadcaster=broadcaster,
        classroom_service=classroom_service,
        occupancy_confidence_threshold=0.5,
    )
    return service, detection_repo


class TestInferenceEventEndpoint:
    """POST /internal/inference/events tests."""

    def test_receive_inference_event(self) -> None:
        """Normal receive test."""
        service, detection_repo = _make_service()
        service._stream_repository.save(_make_stream())

        app.dependency_overrides[get_student_monitoring_service] = lambda: service

        client = TestClient(app)
        response = client.post(
            "/internal/inference/events",
            json={
                "event_id": "test-event-1",
                "camera_id": "camera-a",
                "captured_at": "2026-08-12T01:03:00Z",
                "sequence": 18420,
                "frame": {"width_pixels": 1920, "height_pixels": 1080},
                "detections": [
                    {
                        "detection_id": "det-1",
                        "class_id": 0,
                        "class_name": "person",
                        "confidence": 0.91,
                        "bbox": [100, 120, 300, 600],
                    }
                ],
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["event_id"] == "test-event-1"
        saved = detection_repo.find_by_event_id("test-event-1")
        assert saved is not None
        assert saved.stream_id == "stream-camera-a"
        assert saved.classroom_id == "classroom-a101"

        app.dependency_overrides.clear()

    def test_idempotent_receive(self) -> None:
        """Idempotent receive test."""
        service, _ = _make_service()
        service._stream_repository.save(_make_stream())

        app.dependency_overrides[get_student_monitoring_service] = lambda: service

        client = TestClient(app)
        request_data = {
            "event_id": "test-event-1",
            "camera_id": "camera-a",
            "captured_at": "2026-08-12T01:03:00Z",
            "sequence": 18420,
            "frame": {"width_pixels": 1920, "height_pixels": 1080},
            "detections": [],
        }

        response1 = client.post("/internal/inference/events", json=request_data)
        assert response1.status_code == 201

        response2 = client.post("/internal/inference/events", json=request_data)
        assert response2.status_code == 200

        app.dependency_overrides.clear()

    def test_unknown_camera(self) -> None:
        """Unknown camera test."""
        service, _ = _make_service()

        app.dependency_overrides[get_student_monitoring_service] = lambda: service

        client = TestClient(app)
        response = client.post(
            "/internal/inference/events",
            json={
                "event_id": "test-event-1",
                "camera_id": "unknown-camera",
                "captured_at": "2026-08-12T01:03:00Z",
                "sequence": 18420,
                "frame": {"width_pixels": 1920, "height_pixels": 1080},
                "detections": [],
            },
        )
        assert response.status_code == 404

        app.dependency_overrides.clear()

    def test_empty_detections(self) -> None:
        """Empty detections test."""
        service, _ = _make_service()
        service._stream_repository.save(_make_stream())

        app.dependency_overrides[get_student_monitoring_service] = lambda: service

        client = TestClient(app)
        response = client.post(
            "/internal/inference/events",
            json={
                "event_id": "test-event-empty",
                "camera_id": "camera-a",
                "captured_at": "2026-08-12T01:03:00Z",
                "sequence": 18421,
                "frame": {"width_pixels": 1920, "height_pixels": 1080},
                "detections": [],
            },
        )
        assert response.status_code == 201

        app.dependency_overrides.clear()


class TestVideoSegmentEndpoint:
    """POST /internal/video-segments tests."""

    def test_receive_video_segment(self) -> None:
        """Normal receive test."""
        service, _ = _make_service()
        service._stream_repository.save(_make_stream())

        app.dependency_overrides[get_student_monitoring_service] = lambda: service

        client = TestClient(app)
        response = client.post(
            "/internal/video-segments",
            json={
                "segment_id": "camera-a-20260812T010000Z",
                "camera_id": "camera-a",
                "recorded_from": "2026-08-12T01:00:00Z",
                "recorded_to": "2026-08-12T01:05:00Z",
                "storage": "minio",
                "bucket_alias": "recordings",
                "object_key": "camera-a/2026-08-12/20260812T010000Z.mp4",
                "size_bytes": 48392012,
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["segment_id"] == "camera-a-20260812T010000Z"

        app.dependency_overrides.clear()
