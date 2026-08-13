"""Video monitoring repository ports."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .models import VideoStream


class VideoStreamRepository(Protocol):
    """Video stream repository port."""

    def find_by_id(self, stream_id: str) -> VideoStream | None:
        """Find stream by ID."""
        ...

    def find_by_camera_id(self, camera_id: str) -> VideoStream | None:
        """Find stream by camera ID."""
        ...

    def find_all_enabled(self) -> list[VideoStream]:
        """Find all enabled streams."""
        ...

    def update_last_detection(
        self, camera_id: str, captured_at: datetime
    ) -> None:
        """Update last detection timestamp."""
        ...

    def save(self, stream: VideoStream) -> VideoStream:
        """Save stream (seed and admin registration)."""
        ...
