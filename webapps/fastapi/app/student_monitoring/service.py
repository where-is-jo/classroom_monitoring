"""Student monitoring service."""

from __future__ import annotations

from dataclasses import dataclass

from ..shared.broadcaster import InMemoryBroadcaster
from ..video_monitoring.ports import VideoStreamRepository
from .errors import VideoStreamNotFoundError
from .models import DetectionEvent, VideoSegment
from .ports import DetectionEventRepository, VideoSegmentRepository


@dataclass(frozen=True)
class InferenceEventResult:
    """Result of receiving an inference event."""
    event: DetectionEvent
    is_new: bool


class StudentMonitoringService:
    """Student monitoring service."""

    def __init__(
        self,
        detection_repository: DetectionEventRepository,
        segment_repository: VideoSegmentRepository,
        stream_repository: VideoStreamRepository,
        broadcaster: InMemoryBroadcaster,
    ) -> None:
        self._detection_repository = detection_repository
        self._segment_repository = segment_repository
        self._stream_repository = stream_repository
        self._broadcaster = broadcaster

    def receive_inference_event(self, event: DetectionEvent) -> InferenceEventResult:
        """Receive inference event."""
        # Check camera exists
        stream = self._stream_repository.find_by_camera_id(event.camera_id)
        if stream is None:
            raise VideoStreamNotFoundError()

        # Check if event already exists
        existing = self._detection_repository.find_by_event_id(event.event_id)
        is_new = existing is None

        # Save event (idempotent)
        saved_event = self._detection_repository.save(event)

        # Update last detection timestamp only for new events
        if is_new:
            self._stream_repository.update_last_detection(event.camera_id, event.captured_at)

            # Publish to SSE only for new events
            self._broadcaster.publish({
                "type": "detection",
                "event_id": saved_event.event_id,
                "camera_id": saved_event.camera_id,
                "captured_at": saved_event.captured_at.isoformat(),
                "detections_count": len(saved_event.detections),
            })

        return InferenceEventResult(event=saved_event, is_new=is_new)

    def receive_video_segment(self, segment: VideoSegment) -> VideoSegment:
        """Receive video segment."""
        # Check camera exists
        stream = self._stream_repository.find_by_camera_id(segment.camera_id)
        if stream is None:
            raise VideoStreamNotFoundError()

        # Save segment (idempotent)
        return self._segment_repository.save(segment)
