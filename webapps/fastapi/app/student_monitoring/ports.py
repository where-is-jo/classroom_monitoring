"""Student monitoring repository ports."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .models import (
    DetectionEvent,
    DetectionEventPage,
    VideoSegment,
)


class DetectionEventRepository(Protocol):
    """Detection event repository port."""

    def save(self, event: DetectionEvent) -> DetectionEvent:
        """Save event (idempotent). Same body returns existing, different body raises conflict."""
        ...

    def find_by_event_id(self, event_id: str) -> DetectionEvent | None:
        """Find by event ID."""
        ...

    def find_recent_by_camera(
        self, camera_id: str, limit: int
    ) -> list[DetectionEvent]:
        """Find recent detections by camera."""
        ...

    def find_by_camera_and_period(
        self,
        camera_id: str,
        from_dt: datetime,
        to_dt: datetime,
        *,
        limit: int,
        cursor: str | None,
    ) -> DetectionEventPage:
        """Find detection events by camera and period."""
        ...


class VideoSegmentRepository(Protocol):
    """Video segment repository port."""

    def save(self, segment: VideoSegment) -> VideoSegment:
        """Save segment (idempotent)."""
        ...

    def find_by_camera_and_period(
        self,
        camera_id: str,
        from_dt: datetime,
        to_dt: datetime,
        *,
        limit: int,
    ) -> list[VideoSegment]:
        """Find segments by camera and period."""
        ...
