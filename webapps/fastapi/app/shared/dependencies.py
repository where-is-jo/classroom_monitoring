"""의존성 조립 지점.

**어댑터를 서비스에 연결하는 곳은 여기 한 곳뿐이다.**
주입은 FastAPI `Depends`를 쓴다 (ADR-0002 후속 결정). 별도 DI 컨테이너를 두지 않는다.

저장소를 MongoDB로 바꿀 때 고치는 파일도 여기다.
`get_event_repository`가 반환하는 구현체만 교체하면 서비스와 라우터는 그대로다.

조립을 한곳에 모은 이 파일이 흔히 Composition Root라 부르는 것에 해당한다.
어휘 대응표는 ADR-0005에 있다.
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends

from ..events.adapters.memory_repository import InMemoryEventRepository
from ..events.ports import EventRepository
from ..events.service import EventService
from .config import Settings


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def _event_repository() -> InMemoryEventRepository:
    return InMemoryEventRepository()


def get_event_repository() -> EventRepository:
    return _event_repository()


def get_event_service(
    repository: EventRepository = Depends(get_event_repository),
    settings: Settings = Depends(get_settings),
) -> EventService:
    return EventService(
        repository,
        high_confidence_threshold=settings.high_confidence_threshold,
        medium_confidence_threshold=settings.medium_confidence_threshold,
    )
