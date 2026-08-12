"""In-memory video stream repository."""

from __future__ import annotations

from datetime import datetime

from ..models import VideoStream


class MemoryVideoStreamRepository:
    """In-memory video stream repository."""

    def __init__(self) -> None:
        self._streams: dict[str, VideoStream] = {}

    def find_by_camera_id(self, camera_id: str) -> VideoStream | None:
        """Find stream by camera ID."""
        return self._streams.get(camera_id)

    def find_all_enabled(self) -> list[VideoStream]:
        """Find all enabled streams."""
        return [s for s in self._streams.values() if s.enabled]

    def update_last_detection(
        self, camera_id: str, captured_at: datetime
    ) -> None:
        """Update last detection timestamp."""
        stream = self._streams.get(camera_id)
        if stream:
            self._streams[camera_id] = VideoStream(
                id=stream.id,
                camera_id=stream.camera_id,
                classroom_id=stream.classroom_id,
                camera_label=stream.camera_label,
                playback_kind=stream.playback_kind,
                playback_path=stream.playback_path,
                enabled=stream.enabled,
                last_frame_at=stream.last_frame_at,
                last_detection_at=captured_at,
                is_demo=stream.is_demo,
                created_at=stream.created_at,
                updated_at=datetime.now(stream.created_at.tzinfo),
            )

    def save(self, stream: VideoStream) -> VideoStream:
        """Save stream."""
        self._streams[stream.camera_id] = stream
        return stream
