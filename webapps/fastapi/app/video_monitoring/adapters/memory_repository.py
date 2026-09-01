"""In-memory video stream repository."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from ..models import VideoStream


class MemoryVideoStreamRepository:
    """In-memory video stream repository."""

    def __init__(self) -> None:
        self._streams: dict[str, VideoStream] = {}

    def find_by_id(self, stream_id: str) -> VideoStream | None:
        """Find stream by ID."""
        for stream in self._streams.values():
            if stream.id == stream_id:
                return stream
        return None

    def find_by_camera_id(self, camera_id: str) -> VideoStream | None:
        """Find stream by camera ID."""
        return self._streams.get(camera_id)

    def find_all_enabled(self) -> list[VideoStream]:
        """Find all enabled streams."""
        return [s for s in self._streams.values() if s.enabled]

    def find_monitoring_streams(self) -> list[VideoStream]:
        """실제 모니터링 stream만 반환한다 (enabled=true AND is_demo=false)."""
        return [s for s in self._streams.values() if s.enabled and not s.is_demo]

    def update_last_detection(self, camera_id: str, captured_at: datetime) -> None:
        """마지막 탐지 시각을 과거로 되돌리지 않고 갱신한다."""
        stream = self._streams.get(camera_id)
        if stream and (stream.last_detection_at is None or captured_at > stream.last_detection_at):
            self._streams[camera_id] = replace(
                stream,
                last_detection_at=captured_at,
                updated_at=datetime.now(stream.created_at.tzinfo),
            )

    def save(self, stream: VideoStream) -> VideoStream:
        """Save stream."""
        self._streams[stream.camera_id] = stream
        return stream
