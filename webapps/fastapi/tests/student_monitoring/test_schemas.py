"""Schema validation tests."""

from datetime import UTC, datetime

import pytest

from app.student_monitoring.schemas import (
    DetectionSchema,
    FrameSchema,
    InferenceEventRequest,
    VideoSegmentRequest,
)


class TestInferenceEventRequest:
    """InferenceEventRequest validation tests."""

    def test_valid_request(self) -> None:
        """Valid request test."""
        request = InferenceEventRequest(
            event_id="test-event-1",
            camera_id="camera-a",
            captured_at=datetime(2026, 8, 12, 1, 3, 0, tzinfo=UTC),
            sequence=18420,
            frame=FrameSchema(width_pixels=1920, height_pixels=1080),
            detections=[
                DetectionSchema(
                    detection_id="det-1",
                    class_id=0,
                    class_name="person",
                    confidence=0.91,
                    bbox=(100, 120, 300, 600),
                ),
            ],
        )
        assert request.event_id == "test-event-1"
        assert len(request.detections) == 1

    def test_empty_detections(self) -> None:
        """Empty detections test."""
        request = InferenceEventRequest(
            event_id="test-event-empty",
            camera_id="camera-a",
            captured_at=datetime(2026, 8, 12, 1, 3, 0, tzinfo=UTC),
            sequence=18421,
            frame=FrameSchema(width_pixels=1920, height_pixels=1080),
            detections=[],
        )
        assert len(request.detections) == 0

    def test_bbox_reversed(self) -> None:
        """Reversed bbox test."""
        with pytest.raises(ValueError, match="x_min must be less than x_max"):
            DetectionSchema(
                detection_id="det-1",
                class_id=0,
                class_name="person",
                confidence=0.91,
                bbox=(300, 120, 100, 600),
            )

    def test_bbox_out_of_frame(self) -> None:
        """Negative bbox coordinates test."""
        with pytest.raises(ValueError, match="bbox coordinates must be non-negative"):
            DetectionSchema(
                detection_id="det-1",
                class_id=0,
                class_name="person",
                confidence=0.91,
                bbox=(-100, 120, 300, 600),
            )

    def test_timezone_missing(self) -> None:
        """Missing timezone test."""
        with pytest.raises(ValueError, match="captured_at must have timezone"):
            InferenceEventRequest(
                event_id="test-event-1",
                camera_id="camera-a",
                # 시간대 없는 값을 일부러 넣는다. 검증이 이것을 거부하는지가 이 테스트의 대상이다.
                captured_at=datetime(2026, 8, 12, 1, 3, 0),  # noqa: DTZ001
                sequence=18420,
                frame=FrameSchema(width_pixels=1920, height_pixels=1080),
                detections=[],
            )


class TestVideoSegmentRequest:
    """VideoSegmentRequest validation tests."""

    def test_valid_request(self) -> None:
        """Valid request test."""
        request = VideoSegmentRequest(
            segment_id="camera-a-20260812T010000Z",
            camera_id="camera-a",
            recorded_from=datetime(2026, 8, 12, 1, 0, 0, tzinfo=UTC),
            recorded_to=datetime(2026, 8, 12, 1, 5, 0, tzinfo=UTC),
            storage="minio",
            bucket_alias="recordings",
            object_key="camera-a/2026-08-12/20260812T010000Z.mp4",
            size_bytes=48392012,
        )
        assert request.segment_id == "camera-a-20260812T010000Z"

    def test_timezone_missing(self) -> None:
        """Missing timezone test."""
        with pytest.raises(ValueError, match="Timestamp must have timezone"):
            VideoSegmentRequest(
                segment_id="camera-a-20260812T010000Z",
                camera_id="camera-a",
                # 시간대 없는 값을 일부러 넣는다. 검증이 이것을 거부하는지가 이 테스트의 대상이다.
                recorded_from=datetime(2026, 8, 12, 1, 0, 0),  # noqa: DTZ001
                recorded_to=datetime(2026, 8, 12, 1, 5, 0, tzinfo=UTC),
                storage="minio",
                bucket_alias="recordings",
                object_key="camera-a/2026-08-12/20260812T010000Z.mp4",
                size_bytes=48392012,
            )
