"""인메모리 이벤트 저장소.

`EventRepository` 포트의 구현체다. 외부 의존성 없이 앱을 띄우기 위한 것으로,
로컬 개발과 구조 확인 용도다. **운영에서 사용하지 않는다.**

MongoDB 어댑터를 추가할 때 이 파일을 고치지 않는다. 같은 포트를 구현하는
`mongo_repository.py`를 새로 만들고 `shared/dependencies.py`에서 바꿔 끼운다.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models import Event

# 아래는 실제 탐지 결과가 아니라 화면 확인용 샘플이다.
_SAMPLE_EVENTS: list[Event] = [
    Event(
        id=f"evt-demo-{index:03d}",
        camera_id=f"cam-demo-{(index % 3) + 1:02d}",
        label=label,
        confidence=confidence,
        detected_at=datetime(2026, 8, 5, 9, 0, 0, tzinfo=timezone.utc).replace(
            minute=(index * 7) % 60
        ),
        snapshot_key=f"snapshots/demo/evt-demo-{index:03d}.jpg",
    )
    for index, (label, confidence) in enumerate(
        [
            ("person", 0.94),
            ("person", 0.88),
            ("unattended_bag", 0.72),
            ("person", 0.61),
            ("door_open", 0.83),
            ("person", 0.45),
            ("unattended_bag", 0.55),
            ("person", 0.91),
            ("door_open", 0.38),
            ("person", 0.79),
            ("unattended_bag", 0.66),
            ("person", 0.97),
        ],
        start=1,
    )
]


class InMemoryEventRepository:
    """리스트를 저장소처럼 다루는 구현체."""

    def __init__(self, events: list[Event] | None = None) -> None:
        self._events = list(_SAMPLE_EVENTS if events is None else events)
        self._events.sort(key=lambda event: event.detected_at, reverse=True)

    def list_events(self, *, limit: int, offset: int) -> tuple[list[Event], int]:
        return self._events[offset : offset + limit], len(self._events)

    def get_event(self, event_id: str) -> Event | None:
        for event in self._events:
            if event.id == event_id:
                return event
        return None
