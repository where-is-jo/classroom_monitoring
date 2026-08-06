"""테스트 공통 픽스처.

서비스 계층 테스트는 실제 저장소 없이 돌아간다. 포트를 둔 이유가 이것이다.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

# 앱 시작 전에 외부 의존 없는 local memory mode를 명시한다.
os.environ["APP_ENV"] = "local"
os.environ["DATABASE_MODE"] = "memory"
os.environ["MOCK_INPUTS_ENABLED"] = "false"
os.environ["JWT_ACCESS_SECRET"] = "test-access-secret-at-least-32-characters"
os.environ["JWT_REFRESH_SECRET"] = "test-refresh-secret-at-least-32-characters"
os.environ["CSRF_SECRET"] = "test-csrf-secret-at-least-32-characters"
os.environ["AUDIT_IP_HASH_SECRET"] = "test-audit-secret-at-least-32-characters"
os.environ["WEB_ORIGIN"] = "http://testserver"

import pytest
from fastapi.testclient import TestClient

from app.events.models import Event
from app.events.service import EventService
from app.main import app
from app.shared.dependencies import get_event_repository


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "mongodb: TEST_DATABASE_URL이 있을 때만 실행하는 MongoDB 통합 테스트",
    )


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
        detected_at=datetime(2026, 8, 5, 9, 0, 0, tzinfo=UTC),
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

    def get_event(self, event_id: str) -> Event | None:
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
def client(repository: FakeEventRepository) -> Iterator[TestClient]:
    """라우터 테스트용 클라이언트. 저장소만 대역으로 바꿔 끼운다."""
    app.dependency_overrides[get_event_repository] = lambda: repository
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
