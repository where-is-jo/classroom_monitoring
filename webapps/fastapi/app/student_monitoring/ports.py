"""Student monitoring repository ports."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .models import (
    DetectionEvent,
    DetectionEventPage,
    StudentStateHistory,
    StudentStateRecord,
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

    def find_recent_by_camera(self, camera_id: str, limit: int) -> list[DetectionEvent]:
        """Find recent detections by camera."""
        ...

    def find_recent_by_classroom(
        self,
        classroom_id: str,
        since: datetime,
        *,
        limit: int,
    ) -> list[DetectionEvent]:
        """Find recent detections for a classroom in deterministic newest-first order."""
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


class StudentStateRepository(Protocol):
    """학생 상태 저장소 포트.

    판정 결과와 그 근거 이력을 담는다. 상태는 학생당 하나이므로 갱신은 덮어쓰기이고,
    이력은 상태가 실제로 바뀐 순간에만 쌓인다.
    """

    def list_by_classroom(self, classroom_id: str) -> list[StudentStateRecord]:
        """강의실의 학생별 최신 상태를 반환한다."""
        ...

    def save(self, record: StudentStateRecord) -> StudentStateRecord:
        """학생 상태를 덮어쓴다."""
        ...

    def append_history(self, history: StudentStateHistory) -> StudentStateHistory:
        """상태 전이 이력을 남긴다. 같은 id를 다시 넣어도 한 번만 쌓인다."""
        ...

    def list_history(
        self, classroom_id: str, student_id: str, *, limit: int
    ) -> list[StudentStateHistory]:
        """학생의 상태 전이 이력을 최신순으로 반환한다."""
        ...
