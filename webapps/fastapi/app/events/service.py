"""이벤트 비즈니스 로직.

FastAPI에 의존하지 않는다. `Request`나 `HTTPException`을 여기서 쓰지 않는다.
포트에만 의존하므로 실제 저장소 없이 단위 테스트할 수 있다.
"""

from __future__ import annotations

from ..shared.errors import EventNotFoundError
from .models import EventPage, EventSummary
from .ports import EventRepository


class EventService:
    def __init__(
        self,
        repository: EventRepository,
        *,
        high_confidence_threshold: float,
        medium_confidence_threshold: float,
    ) -> None:
        self._repository = repository
        self._high = high_confidence_threshold
        self._medium = medium_confidence_threshold

    def list_events(self, *, limit: int, offset: int) -> EventPage:
        events, total = self._repository.list_events(limit=limit, offset=offset)
        return EventPage(
            items=[self._summarize(event) for event in events],
            total=total,
        )

    def get_event(self, event_id: str) -> EventSummary:
        event = self._repository.get_event(event_id)
        if event is None:
            raise EventNotFoundError(event_id)
        return self._summarize(event)

    def _summarize(self, event) -> EventSummary:
        return EventSummary(event=event, confidence_level=self._classify(event.confidence))

    def _classify(self, confidence: float) -> str:
        """신뢰도를 등급으로 바꾼다.

        임계값은 설정에서 주입받는다. 이 판단을 템플릿이나 스키마에 두지 않는다.
        기준이 바뀌어도 화면과 API를 고치지 않기 위해서다.
        """
        if confidence >= self._high:
            return "high"
        if confidence >= self._medium:
            return "medium"
        return "low"
