"""입구 얼굴 관측 이벤트 저장소 포트."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .models import EntryIdentityEvent, EntryIdentityEventPage, EntryIdentityStatus


class EntryIdentityEventRepository(Protocol):
    def save(self, event: EntryIdentityEvent) -> tuple[EntryIdentityEvent, bool]:
        """신규 이벤트와 생성 여부를 반환하고 다른 본문이면 충돌을 낸다."""
        ...

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
        """최신 촬영 시각 순으로 조건에 맞는 이벤트를 조회한다."""
        ...
