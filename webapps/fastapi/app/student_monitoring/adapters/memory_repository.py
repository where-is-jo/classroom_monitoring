"""In-memory repository implementations."""

from __future__ import annotations

from datetime import datetime

from ..errors import InferenceEventConflictError
from ..models import (
    DetectionEvent,
    DetectionEventPage,
    VideoSegment,
)


class MemoryDetectionEventRepository:
    """In-memory detection event repository."""

    def __init__(self) -> None:
        self._events: dict[str, DetectionEvent] = {}

    def save(self, event: DetectionEvent) -> DetectionEvent:
        """Save event (idempotent)."""
        existing = self._events.get(event.event_id)
        if existing is None:
            self._events[event.event_id] = event
            return event
        # Check if same body
        if (
            existing.camera_id != event.camera_id
            or existing.captured_at != event.captured_at
            or existing.sequence != event.sequence
            or existing.frame != event.frame
            or existing.detections != event.detections
        ):
            raise InferenceEventConflictError()
        return existing

    def find_by_event_id(self, event_id: str) -> DetectionEvent | None:
        """Find by event ID."""
        return self._events.get(event_id)

    def find_recent_by_camera(
        self, camera_id: str, limit: int
    ) -> list[DetectionEvent]:
        """Find recent detections by camera."""
        events = [
            e for e in self._events.values() if e.camera_id == camera_id
        ]
        events.sort(key=lambda e: e.captured_at, reverse=True)
        return events[:limit]

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
        events = [
            e
            for e in self._events.values()
            if e.camera_id == camera_id and from_dt <= e.captured_at < to_dt
        ]
        events.sort(key=lambda e: e.captured_at, reverse=True)

        # Apply cursor
        if cursor:
            cursor_idx = None
            for i, e in enumerate(events):
                if e.event_id == cursor:
                    cursor_idx = i + 1
                    break
            if cursor_idx is not None:
                events = events[cursor_idx:]

        total = len(events)
        items = events[:limit]
        next_cursor = items[-1].event_id if len(items) == limit and limit < total else None

        return DetectionEventPage(items=items, total=total, next_cursor=next_cursor)


class MemoryVideoSegmentRepository:
    """In-memory video segment repository."""

    def __init__(self) -> None:
        self._segments: dict[str, VideoSegment] = {}

    def save(self, segment: VideoSegment) -> VideoSegment:
        """Save segment (idempotent)."""
        existing = self._segments.get(segment.segment_id)
        if existing is None:
            self._segments[segment.segment_id] = segment
            return segment
        return existing

    def find_by_camera_and_period(
        self,
        camera_id: str,
        from_dt: datetime,
        to_dt: datetime,
        *,
        limit: int,
    ) -> list[VideoSegment]:
        """Find segments by camera and period."""
        segments = [
            s
            for s in self._segments.values()
            if s.camera_id == camera_id and s.recorded_from >= from_dt and s.recorded_from < to_dt
        ]
        segments.sort(key=lambda s: s.recorded_from, reverse=True)
        return segments[:limit]
