"""이벤트 기능이 프로세스 밖과 통신하는 지점.

포트는 프로세스 외부 I/O 경계에만 만든다. 판단 기준은 ADR-0002에 있다.
서비스 계층은 이 Protocol에만 의존하고 어댑터 구현체를 직접 import하지 않는다.

`EventRepository`는 흔히 Repository 패턴이라 부르는 것과 같다. 다만 이 저장소는
코드와 문서에서 "저장소 포트"라는 이름만 쓴다. 어휘 대응표는 ADR-0005에 있다.
"""

from __future__ import annotations

from typing import Protocol

from .models import Event


class EventRepository(Protocol):
    """탐지 이벤트 저장소.

    구현체는 `adapters/` 아래에 둔다. 시그니처에 저장 기술의 형태
    (MongoDB 문서, HTTP 응답 등)를 노출하지 않고 도메인 타입만 주고받는다.
    """

    def list_events(self, *, limit: int, offset: int) -> tuple[list[Event], int]:
        """이벤트 목록과 전체 건수를 반환한다."""
        ...

    def get_event(self, event_id: str) -> Event | None:
        """이벤트 한 건을 반환한다. 없으면 None."""
        ...
