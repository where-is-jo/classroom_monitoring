"""테스트 공통 픽스처.

서비스 계층 테스트는 실제 저장소 없이 돌아간다. 포트를 둔 이유가 이것이다.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.events.models import Event
from app.events.service import EventService
from app.main import app
from app.shared.dependencies import get_event_repository


def make_event(
    event_id: str = "evt-test-001",
    *,
    confidence: float = 0.9,
    camera_id: str = "cam-test-01",
    label: str = "person",
) -> Event:
    return Event(
        id=event_id,
        camera_id=camera_id,
        label=label,
        confidence=confidence,
        detected_at=datetime(2026, 8, 5, 9, 0, 0, tzinfo=timezone.utc),
        snapshot_key=f"snapshots/test/{event_id}.jpg",
    )


class FakeEventRepository:
    """EventRepository 포트의 테스트 대역.

    Protocol을 상속하지 않아도 된다. 구조만 맞으면 된다.
    """

    def __init__(self, events: list[Event] | None = None) -> None:
        self.events = list(events or [])

    def list_events(self, *, limit: int, offset: int) -> tuple[list[Event], int]:
        return self.events[offset : offset + limit], len(self.events)

    def get_event(self, event_id: str):
        return next((e for e in self.events if e.id == event_id), None)


@pytest.fixture
def repository() -> FakeEventRepository:
    return FakeEventRepository([make_event(f"evt-test-{i:03d}") for i in range(1, 6)])


@pytest.fixture
def service(repository: FakeEventRepository) -> EventService:
    return EventService(
        repository,
        high_confidence_threshold=0.80,
        medium_confidence_threshold=0.50,
    )


@pytest.fixture
def client(repository: FakeEventRepository):
    """라우터 테스트용 클라이언트. 저장소만 대역으로 바꿔 끼운다."""
    app.dependency_overrides[get_event_repository] = lambda: repository
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
