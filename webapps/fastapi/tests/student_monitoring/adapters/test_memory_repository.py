"""Memory repository tests."""

from datetime import UTC, datetime

import pytest

from app.student_monitoring.adapters.memory_repository import (
    MemoryDetectionEventRepository,
    MemoryVideoSegmentRepository,
)
from app.student_monitoring.errors import InferenceEventConflictError
from app.student_monitoring.models import (
    Detection,
    DetectionEvent,
    FrameInfo,
    VideoSegment,
)


def _make_event(
    event_id: str = "event-1",
    camera_id: str = "camera-a",
    sequence: int = 1,
    detections: tuple[Detection, ...] = (),
    classroom_id: str = "classroom-a101",
    captured_at: datetime | None = None,
) -> DetectionEvent:
    return DetectionEvent(
        event_id=event_id,
        camera_id=camera_id,
        stream_id="stream-camera-a",
        classroom_id=classroom_id,
        captured_at=captured_at or datetime(2026, 8, 12, 1, 3, 0, tzinfo=UTC),
        sequence=sequence,
        frame=FrameInfo(width_pixels=1920, height_pixels=1080),
        detections=detections,
        received_at=datetime(2026, 8, 12, 1, 3, 1, tzinfo=UTC),
        schema_version=1,
    )


def _make_segment(
    segment_id: str = "segment-1",
    camera_id: str = "camera-a",
) -> VideoSegment:
    return VideoSegment(
        segment_id=segment_id,
        camera_id=camera_id,
        stream_id="stream-camera-a",
        classroom_id="classroom-a101",
        recorded_from=datetime(2026, 8, 12, 1, 0, 0, tzinfo=UTC),
        recorded_to=datetime(2026, 8, 12, 1, 5, 0, tzinfo=UTC),
        storage="minio",
        bucket_alias="recordings",
        object_key=f"{camera_id}/2026-08-12/{segment_id}.mp4",
        size_bytes=48392012,
        received_at=datetime(2026, 8, 12, 1, 5, 1, tzinfo=UTC),
        schema_version=1,
    )


class TestMemoryDetectionEventRepository:
    """MemoryDetectionEventRepository tests."""

    def test_save_and_find(self) -> None:
        """Save and find test."""
        repo = MemoryDetectionEventRepository()
        event = _make_event()
        repo.save(event)
        found = repo.find_by_event_id("event-1")
        assert found is not None
        assert found.event_id == "event-1"

    def test_idempotent_save(self) -> None:
        """Idempotent save test."""
        repo = MemoryDetectionEventRepository()
        event = _make_event()
        result1 = repo.save(event)
        result2 = repo.save(event)
        assert result1.event_id == result2.event_id

    def test_conflict_detection(self) -> None:
        """Conflict detection test."""
        repo = MemoryDetectionEventRepository()
        event1 = _make_event(sequence=1)
        event2 = _make_event(sequence=2)  # Same event_id, different sequence
        repo.save(event1)
        with pytest.raises(InferenceEventConflictError):
            repo.save(event2)

    def test_empty_detections(self) -> None:
        """Empty detections save test."""
        repo = MemoryDetectionEventRepository()
        event = _make_event(detections=())
        result = repo.save(event)
        assert len(result.detections) == 0

    def test_find_nonexistent(self) -> None:
        """Find nonexistent event test."""
        repo = MemoryDetectionEventRepository()
        found = repo.find_by_event_id("nonexistent")
        assert found is None

    def test_find_recent_by_camera(self) -> None:
        """Find recent by camera test."""
        repo = MemoryDetectionEventRepository()
        repo.save(_make_event(event_id="event-1", camera_id="camera-a"))
        repo.save(_make_event(event_id="event-2", camera_id="camera-a"))
        repo.save(_make_event(event_id="event-3", camera_id="camera-b"))
        events = repo.find_recent_by_camera("camera-a", limit=10)
        assert len(events) == 2

    def test_find_recent_by_classroom_filters_stale_and_is_deterministic(self) -> None:
        repo = MemoryDetectionEventRepository()
        same_time = datetime(2026, 8, 12, 1, 3, 0, tzinfo=UTC)
        repo.save(_make_event(event_id="event-b", captured_at=same_time))
        repo.save(_make_event(event_id="event-a", captured_at=same_time))
        repo.save(
            _make_event(
                event_id="event-stale",
                captured_at=datetime(2026, 8, 12, 0, 0, tzinfo=UTC),
            )
        )
        repo.save(_make_event(event_id="event-other", classroom_id="classroom-b203"))

        events = repo.find_recent_by_classroom(
            "classroom-a101",
            datetime(2026, 8, 12, 1, 0, tzinfo=UTC),
            limit=10,
        )

        assert [event.event_id for event in events] == ["event-a", "event-b"]


class TestMemoryVideoSegmentRepository:
    """MemoryVideoSegmentRepository tests."""

    def test_save_and_find(self) -> None:
        """Save and find test."""
        repo = MemoryVideoSegmentRepository()
        segment = _make_segment()
        repo.save(segment)
        segments = repo.find_by_camera_and_period(
            "camera-a",
            datetime(2026, 8, 12, 0, 0, 0, tzinfo=UTC),
            datetime(2026, 8, 12, 2, 0, 0, tzinfo=UTC),
            limit=10,
        )
        assert len(segments) == 1

    def test_idempotent_save(self) -> None:
        """Idempotent save test."""
        repo = MemoryVideoSegmentRepository()
        segment = _make_segment()
        result1 = repo.save(segment)
        result2 = repo.save(segment)
        assert result1.segment_id == result2.segment_id
