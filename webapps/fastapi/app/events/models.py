"""탐지 이벤트 도메인 모델.

프레임워크와 저장소에 의존하지 않는 평범한 dataclass다.
서비스 계층과 포트가 주고받는 타입이며, Pydantic 스키마는 경계에서만 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Event:
    """저장소에서 읽어온 탐지 이벤트 한 건."""

    id: str
    camera_id: str
    label: str
    confidence: float
    detected_at: datetime  # UTC
    snapshot_key: str | None = None


@dataclass(frozen=True)
class EventSummary:
    """서비스가 판단을 마친 이벤트.

    `confidence_level`은 임계값을 적용한 결과다. 이 판단은 서비스 계층에서 끝내고
    화면과 API는 결과만 표시한다.
    """

    event: Event
    confidence_level: str  # "high" | "medium" | "low"


@dataclass(frozen=True)
class EventPage:
    """페이지네이션된 조회 결과."""

    items: list[EventSummary]
    total: int
