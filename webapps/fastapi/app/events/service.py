"""이벤트 비즈니스 로직.

FastAPI에 의존하지 않는다. `Request`나 `HTTPException`을 여기서 쓰지 않는다.
포트에만 의존하므로 실제 저장소 없이 단위 테스트할 수 있다.

이 클래스가 라우터에 대한 파사드 역할을 한다. 라우터는 메서드 하나만 알면 되고
포트 조합과 호출 순서는 여기 안에 있다. 서비스 위에 별도 파사드를 두지 않는다
(ADR-0005).
"""

from __future__ import annotations

from ..shared.errors import EventNotFoundError
from .models import Event, EventPage, EventSummary
from .ports import EventRepository
from .rules import classify_confidence


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

    def _summarize(self, event: Event) -> EventSummary:
        """도메인 이벤트에 해석 결과를 붙인다.

        해석 규칙 자체는 `rules.py`에 있다. 서비스는 규칙을 호출할 뿐
        판정 방법을 알지 않는다. 임계값은 설정에서 주입받아 넘긴다.
        """
        return EventSummary(
            event=event,
            confidence_level=classify_confidence(
                event.confidence,
                high_threshold=self._high,
                medium_threshold=self._medium,
            ),
        )
