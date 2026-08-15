"""강의실별 학생 상태 SSE 필터·heartbeat·정리 계약."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Iterator
from typing import cast

import pytest
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.classrooms.errors import ClassroomNotFoundError
from app.main import app
from app.shared.broadcaster import InMemoryBroadcaster
from app.shared.config import Settings
from app.shared.dependencies import get_broadcaster, get_settings, get_student_monitoring_service
from app.student_monitoring.router import stream_student_state_events
from app.student_monitoring.service import StudentMonitoringService

CLASSROOM_ID = "classroom-a101"


class StubStudentMonitoringService:
    def list_student_states(self, classroom_id: str) -> list[object]:
        if classroom_id != CLASSROOM_ID:
            raise ClassroomNotFoundError()
        return []


@pytest.fixture
def service() -> StudentMonitoringService:
    return cast(StudentMonitoringService, StubStudentMonitoringService())


@pytest.fixture
def broadcaster() -> InMemoryBroadcaster:
    return InMemoryBroadcaster()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        app_env="local",
        database_mode="memory",
        sse_heartbeat_interval_seconds=1,
        sse_reconnection_timeout_seconds=2,
    )


@pytest.fixture
def client(
    service: StudentMonitoringService,
    broadcaster: InMemoryBroadcaster,
    settings: Settings,
) -> Iterator[TestClient]:
    app.dependency_overrides[get_student_monitoring_service] = lambda: service
    app.dependency_overrides[get_broadcaster] = lambda: broadcaster
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _payload(*, event_id: str, classroom_id: str) -> dict[str, object]:
    return {
        "type": "student-state",
        "event_id": event_id,
        "classroom_id": classroom_id,
        "student_id": "student-1",
        "student_name": "김로운",
        "student_no": "20260001",
        "assigned_seat_id": "seat-1",
        "assigned_seat_label": "좌석 S01",
        "current_seat_id": "seat-1",
        "current_state": "PRESENT",
        "confidence": 0.91,
        "observed_at": "2026-08-15T07:30:00+00:00",
    }


def _stream(
    service: StudentMonitoringService,
    broadcaster: InMemoryBroadcaster,
    settings: Settings,
) -> tuple[StreamingResponse, AsyncGenerator[str, None]]:
    async def _open() -> tuple[StreamingResponse, AsyncGenerator[str, None]]:
        response = await stream_student_state_events(
            CLASSROOM_ID,
            service,
            broadcaster,
            settings,
        )
        return response, cast(AsyncGenerator[str, None], response.body_iterator)

    return asyncio.run(_open())


def test_unknown_classroom_returns_existing_404_envelope(client: TestClient) -> None:
    with client.stream(
        "GET", "/api/v1/classrooms/classroom-missing/student-state-events"
    ) as response:
        assert response.status_code == 404
        response.read()
        assert response.json()["error"]["code"] == "CLASSROOM_NOT_FOUND"


def test_stream_headers_retry_and_matching_classroom_event(
    service: StudentMonitoringService,
    broadcaster: InMemoryBroadcaster,
    settings: Settings,
) -> None:
    response, generator = _stream(service, broadcaster, settings)
    payload = _payload(event_id="event-1", classroom_id=CLASSROOM_ID)

    async def _run() -> list[str]:
        retry = await anext(generator)
        broadcaster.publish(payload)
        lines = [retry, await anext(generator), await anext(generator), await anext(generator)]
        await generator.aclose()
        return lines

    lines = asyncio.run(_run())
    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
    assert lines[:3] == [
        "retry: 2000\n\n",
        "id: event-1\n",
        "event: student-state\n",
    ]
    assert json.loads(lines[3][len("data: ") :]) == payload


def test_other_classroom_and_other_event_type_are_filtered_to_heartbeat(
    service: StudentMonitoringService,
    broadcaster: InMemoryBroadcaster,
    settings: Settings,
) -> None:
    _, generator = _stream(service, broadcaster, settings)

    async def _run() -> str:
        await anext(generator)
        broadcaster.publish(_payload(event_id="other", classroom_id="classroom-b203"))
        broadcaster.publish(
            {"type": "occupancy", "event_id": "occupancy", "classroom_id": CLASSROOM_ID}
        )
        heartbeat = await anext(generator)
        await generator.aclose()
        return heartbeat

    assert asyncio.run(_run()) == ": heartbeat\n\n"


def test_subscriber_is_removed_when_stream_closes(
    service: StudentMonitoringService,
    broadcaster: InMemoryBroadcaster,
    settings: Settings,
) -> None:
    _, generator = _stream(service, broadcaster, settings)

    async def _run() -> None:
        await anext(generator)
        assert len(broadcaster._subscribers) == 1
        await generator.aclose()

    asyncio.run(_run())
    assert broadcaster._subscribers == []
