"""입구 얼굴 관측 이벤트의 memory 저장소."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from ..errors import EntryIdentityEventConflictError
from ..models import (
    EntryIdentityEvent,
    EntryIdentityEventPage,
    EntryIdentityStatus,
    same_event_body,
)


class InMemoryEntryIdentityEventRepository:
    def __init__(self, *, clock: Callable[[], datetime]) -> None:
        self._events: dict[str, EntryIdentityEvent] = {}
        self._clock = clock

    def save(self, event: EntryIdentityEvent) -> tuple[EntryIdentityEvent, bool]:
        self._purge_expired()
        existing = self._events.get(event.event_id)
        if existing is None:
            self._events[event.event_id] = event
            return event, True
        if not same_event_body(existing, event):
            raise EntryIdentityEventConflictError()
        return existing, False

    def list_by_stream(
        self,
        stream_id: str,
        *,
        status: EntryIdentityStatus | None,
        student_id: str | None,
        from_at: datetime | None,
        to_at: datetime | None,
        limit: int,
        cursor: str | None,
    ) -> EntryIdentityEventPage:
        self._purge_expired()
        events = [
            event
            for event in self._events.values()
            if event.stream_id == stream_id
            and (from_at is None or event.captured_at >= from_at)
            and (to_at is None or event.captured_at <= to_at)
            and self._matches_observation(
                event,
                status=status,
                student_id=student_id,
            )
        ]
        events.sort(
            key=lambda event: (event.captured_at, event.event_id),
            reverse=True,
        )
        total = len(events)
        if cursor is not None:
            cursor_index = next(
                (index for index, event in enumerate(events) if event.event_id == cursor),
                None,
            )
            events = [] if cursor_index is None else events[cursor_index + 1 :]
        items = events[:limit]
        next_cursor = items[-1].event_id if len(events) > len(items) and items else None
        return EntryIdentityEventPage(items, total, next_cursor)

    def _purge_expired(self) -> None:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("memory 저장소 clock에는 timezone이 필요합니다.")
        now = now.astimezone(UTC)
        self._events = {
            event_id: event for event_id, event in self._events.items() if event.expires_at > now
        }

    @staticmethod
    def _matches_observation(
        event: EntryIdentityEvent,
        *,
        status: EntryIdentityStatus | None,
        student_id: str | None,
    ) -> bool:
        if status is None and student_id is None:
            return True
        return any(
            (status is None or observation.identity_status is status)
            and (student_id is None or observation.student_id == student_id)
            for observation in event.observations
        )
