"""Domain model tests."""

from datetime import datetime, timezone

from app.student_monitoring.models import (
    Detection,
    DetectionEvent,
    FrameInfo,
    VideoSegment,
)
from app.student_monitoring.errors import (
    InferenceEventConflictError,
    RepositoryError,
    VideoStreamNotFoundError,
)


class TestDetectionEvent:
    """DetectionEvent model tests."""

    def test_create_detection_event(self) -> None:
        """DetectionEvent creation test."""
        event = DetectionEvent(
            event_id="test-event-1",
            camera_id="camera-a",
            stream_id="stream-camera-a",
            classroom_id="classroom-a101",
            captured_at=datetime(2026, 8, 12, 1, 3, 0, tzinfo=timezone.utc),
            sequence=18420,
            frame=FrameInfo(width_pixels=1920, height_pixels=1080),
            detections=(
                Detection(
                    detection_id="det-1",
                    class_id=0,
                    class_name="person",
                    confidence=0.91,
                    bbox=(100, 120, 300, 600),
                    student_id=None,
                    identity_confidence=None,
                    face_bbox=None,
                ),
            ),
            received_at=datetime(2026, 8, 12, 1, 3, 1, tzinfo=timezone.utc),
            schema_version=1,
        )
        assert event.event_id == "test-event-1"
        assert event.camera_id == "camera-a"
        assert len(event.detections) == 1

    def test_frozen_detection_event(self) -> None:
        """DetectionEvent frozen property test."""
        event = DetectionEvent(
            event_id="test-event-1",
            camera_id="camera-a",
            stream_id="stream-camera-a",
            classroom_id="classroom-a101",
            captured_at=datetime(2026, 8, 12, 1, 3, 0, tzinfo=timezone.utc),
            sequence=18420,
            frame=FrameInfo(width_pixels=1920, height_pixels=1080),
            detections=(),
            received_at=datetime(2026, 8, 12, 1, 3, 1, tzinfo=timezone.utc),
            schema_version=1,
        )
        try:
            event.event_id = "changed"  # type: ignore[misc]
            assert False, "Should raise FrozenInstanceError"
        except AttributeError:
            pass

    def test_empty_detections(self) -> None:
        """Zero detection event test."""
        event = DetectionEvent(
            event_id="test-event-empty",
            camera_id="camera-a",
            stream_id="stream-camera-a",
            classroom_id="classroom-a101",
            captured_at=datetime(2026, 8, 12, 1, 3, 0, tzinfo=timezone.utc),
            sequence=18421,
            frame=FrameInfo(width_pixels=1920, height_pixels=1080),
            detections=(),
            received_at=datetime(2026, 8, 12, 1, 3, 1, tzinfo=timezone.utc),
            schema_version=1,
        )
        assert len(event.detections) == 0


class TestVideoSegment:
    """VideoSegment model tests."""

    def test_create_video_segment(self) -> None:
        """VideoSegment creation test."""
        segment = VideoSegment(
            segment_id="camera-a-20260812T010000Z",
            camera_id="camera-a",
            stream_id="stream-camera-a",
            classroom_id="classroom-a101",
            recorded_from=datetime(2026, 8, 12, 1, 0, 0, tzinfo=timezone.utc),
            recorded_to=datetime(2026, 8, 12, 1, 5, 0, tzinfo=timezone.utc),
            storage="minio",
            bucket_alias="recordings",
            object_key="camera-a/2026-08-12/20260812T010000Z.mp4",
            size_bytes=48392012,
            received_at=datetime(2026, 8, 12, 1, 5, 1, tzinfo=timezone.utc),
            schema_version=1,
        )
        assert segment.segment_id == "camera-a-20260812T010000Z"
        assert segment.storage == "minio"


class TestDomainErrors:
    """Domain error hierarchy tests."""

    def test_inference_event_conflict_error(self) -> None:
        """InferenceEventConflictError test."""
        error = InferenceEventConflictError()
        assert error.code == "INFERENCE_EVENT_CONFLICT"
        assert error.status_code == 409

    def test_video_stream_not_found_error(self) -> None:
        """VideoStreamNotFoundError test."""
        error = VideoStreamNotFoundError()
        assert error.code == "VIDEO_STREAM_NOT_FOUND"
        assert error.status_code == 404

    def test_repository_error(self) -> None:
        """RepositoryError test."""
        error = RepositoryError()
        assert error.code == "REPOSITORY_ERROR"
        assert error.status_code == 503