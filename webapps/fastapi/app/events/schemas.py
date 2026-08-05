"""HTTP 경계의 요청·응답 스키마.

경계에만 둔다. 서비스 계층으로 넘기지 않는다.
필드 이름과 시각 형식은 docs/conventions/api-convention.md를 따른다.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, field_serializer

from .models import EventPage, EventSummary


class EventResponse(BaseModel):
    id: str
    camera_id: str
    label: str
    confidence: float
    confidence_level: str
    detected_at: datetime
    snapshot_key: str | None

    @field_serializer("detected_at")
    def _serialize_detected_at(self, value: datetime) -> str:
        """ISO 8601 UTC로 직렬화한다 (예: 2026-08-05T09:00:00Z)."""
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")

    @classmethod
    def from_summary(cls, summary: EventSummary) -> "EventResponse":
        event = summary.event
        return cls(
            id=event.id,
            camera_id=event.camera_id,
            label=event.label,
            confidence=event.confidence,
            confidence_level=summary.confidence_level,
            detected_at=event.detected_at,
            snapshot_key=event.snapshot_key,
        )


class EventListResponse(BaseModel):
    items: list[EventResponse]
    total: int
    limit: int
    offset: int

    @classmethod
    def from_page(cls, page: EventPage, *, limit: int, offset: int) -> "EventListResponse":
        return cls(
            items=[EventResponse.from_summary(summary) for summary in page.items],
            total=page.total,
            limit=limit,
            offset=offset,
        )
