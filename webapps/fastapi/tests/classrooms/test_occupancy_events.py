"""강의실 좌석 점유 SSE 엔드포인트 테스트.

`GET /api/v1/classrooms/{classroom_id}/occupancy-events`가
broadcaster의 occupancy 이벤트를 강의실별로 필터링해 SSE 형식으로 전달하고,
이벤트가 없으면 heartbeat를 보내며, 연결이 끊기면 구독자를 정리하는지 검증한다.

SSE 스트림은 종료되지 않으므로 httpx ASGITransport(전체 본문 버퍼링)로는
HTTP 클라이언트 테스트를 할 수 없다. 그래서 엔드포인트 함수를 직접 호출해
`StreamingResponse.body_iterator`를 순회한다. 대상 없는 강의실의 404 매핑만
HTTP 계층(`TestClient.stream`)으로 검증한다.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Iterator
from datetime import UTC, datetime
from typing import cast

import pytest
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.classrooms.adapters.memory_repository import InMemoryClassroomRepository
from app.classrooms.errors import ClassroomNotFoundError
from app.classrooms.models import CreateClassroomCommand, CreateSeatCommand, SeatGeometry
from app.classrooms.router import stream_occupancy_events
from app.classrooms.service import ClassroomService
from app.main import app
from app.shared.broadcaster import InMemoryBroadcaster
from app.shared.config import Settings
from app.shared.dependencies import get_broadcaster, get_classroom_service, get_settings

_CLASSROOM_ID = "classroom-a101"
_SEAT_ID = "seat-1"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        app_env="local",
        database_mode="memory",
        sse_heartbeat_interval_seconds=1,
        sse_reconnection_timeout_seconds=2,
    )


@pytest.fixture
def broadcaster() -> InMemoryBroadcaster:
    return InMemoryBroadcaster()


@pytest.fixture
def service() -> ClassroomService:
    classroom_service = ClassroomService(
        InMemoryClassroomRepository(),
        occupancy_confidence_threshold=0.5,
        clock=lambda: datetime(2026, 8, 13, 9, 0, tzinfo=UTC),
    )
    classroom_service.seed_classroom(
        CreateClassroomCommand(
            id=_CLASSROOM_ID, code="A101", name="일반 강의실", location="A동 1층"
        )
    )
    classroom_service.seed_seat(
        CreateSeatCommand(
            id=_SEAT_ID,
            classroom_id=_CLASSROOM_ID,
            code="S01",
            label="좌석 1",
            geometry=SeatGeometry(x=0.1, y=0.1, width=0.2, height=0.2),
        )
    )
    return classroom_service


@pytest.fixture
def client(
    service: ClassroomService, settings: Settings, broadcaster: InMemoryBroadcaster
) -> Iterator[TestClient]:
    app.dependency_overrides[get_classroom_service] = lambda: service
    app.dependency_overrides[get_broadcaster] = lambda: broadcaster
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _occupancy_event(
    *, event_id: str, classroom_id: str, seat_id: str = _SEAT_ID
) -> dict[str, object]:
    return {
        "type": "occupancy",
        "event_id": event_id,
        "classroom_id": classroom_id,
        "seat_id": seat_id,
        "state": "OCCUPIED",
        "confidence": 0.85,
        "observed_at": "2026-08-13T09:05:00+00:00",
    }


def _stream(
    service: ClassroomService,
    broadcaster: InMemoryBroadcaster,
    settings: Settings,
) -> tuple[StreamingResponse, AsyncGenerator[str, None]]:
    """엔드포인트를 실행해 StreamingResponse와 body_iterator를 돌려준다.

    body_iterator는 엔드포인트가 만든 async generator가 그대로 담기므로
    aclose를 쓸 수 있도록 AsyncGenerator로 좁힌다.
    """

    async def _open() -> tuple[StreamingResponse, AsyncGenerator[str, None]]:
        response = await stream_occupancy_events(_CLASSROOM_ID, service, broadcaster, settings)
        gen = cast(AsyncGenerator[str, None], response.body_iterator)
        return response, gen

    return asyncio.run(_open())


def test_list_page_renders_sse_wiring(client: TestClient) -> None:
    """좌석 현황 화면은 SSE 구독·갱신용 data 속성과 classrooms.js를 렌더링한다."""
    response = client.get(f"/classrooms?classroom_id={_CLASSROOM_ID}")

    assert response.status_code == 200
    assert f'data-classroom-id="{_CLASSROOM_ID}"' in response.text
    assert f'data-seat-id="{_SEAT_ID}"' in response.text
    assert 'data-seat-state="unknown"' in response.text
    assert 'class="state-label"' in response.text
    assert 'class="state-icon"' in response.text
    assert "/static/classrooms.js" in response.text


def test_list_page_renders_sse_wiring_for_non_geometry_seat(client: TestClient) -> None:
    """좌표 없는 일반 좌석 카드도 SSE 갱신 대상과 실시간 집계를 렌더링한다."""
    response = client.post(
        f"/api/v1/classrooms/{_CLASSROOM_ID}/seats",
        json={"code": "S02", "label": "좌석 2"},
    )
    assert response.status_code == 201
    seat_id = response.json()["id"]

    page = client.get(f"/classrooms?classroom_id={_CLASSROOM_ID}")

    assert page.status_code == 200
    assert f'data-seat-id="{seat_id}"' in page.text
    assert 'data-seat-state="unknown"' in page.text
    assert 'data-occupancy-count="occupied"' in page.text
    assert 'data-occupancy-count="vacant"' in page.text
    assert 'data-occupancy-count="unknown"' in page.text
    assert "실시간으로 확인하는 대시보드" in page.text


def test_unknown_classroom_returns_not_found(client: TestClient) -> None:
    """존재하지 않는 강의실의 SSE 구독은 404를 돌려준다."""
    with client.stream("GET", "/api/v1/classrooms/missing/occupancy-events") as response:
        assert response.status_code == 404


def test_unknown_classroom_raises_domain_error(
    service: ClassroomService, broadcaster: InMemoryBroadcaster, settings: Settings
) -> None:
    """존재하지 않는 강의실이면 엔드포인트는 ClassroomNotFoundError를 던진다."""

    async def _run() -> None:
        await stream_occupancy_events("missing", service, broadcaster, settings)

    with pytest.raises(ClassroomNotFoundError):
        asyncio.run(_run())


def test_stream_headers_and_retry(
    service: ClassroomService, broadcaster: InMemoryBroadcaster, settings: Settings
) -> None:
    """스트림 응답은 SSE 미디어 타입·캐시 방지 헤더와 retry 지시로 시작한다."""
    response, gen = _stream(service, broadcaster, settings)

    async def _run() -> str:
        first = await anext(gen)
        await gen.aclose()
        return first

    assert response.status_code == 200
    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
    assert asyncio.run(_run()) == "retry: 2000\n\n"


def test_occupancy_event_delivered_for_matching_classroom(
    service: ClassroomService, broadcaster: InMemoryBroadcaster, settings: Settings
) -> None:
    """같은 강의실의 occupancy 이벤트는 id·event 이름·JSON data로 전달된다."""
    payload = _occupancy_event(event_id="event-1", classroom_id=_CLASSROOM_ID)
    _, gen = _stream(service, broadcaster, settings)

    async def _run() -> list[str]:
        await anext(gen)  # retry
        broadcaster.publish(payload)
        lines = [await anext(gen), await anext(gen), await anext(gen)]
        await gen.aclose()
        return lines

    lines = asyncio.run(_run())
    assert lines[0] == "id: event-1\n"
    assert lines[1] == "event: occupancy\n"
    data = json.loads(lines[2][len("data: ") :])
    assert data["type"] == "occupancy"
    assert data["classroom_id"] == _CLASSROOM_ID
    assert data["seat_id"] == _SEAT_ID
    assert data["state"] == "OCCUPIED"
    assert data["confidence"] == 0.85
    assert data["observed_at"] == "2026-08-13T09:05:00+00:00"


def test_other_classroom_event_is_filtered_and_heartbeat_emitted(
    service: ClassroomService, broadcaster: InMemoryBroadcaster, settings: Settings
) -> None:
    """다른 강의실 이벤트는 전달되지 않고, 이벤트가 없으면 heartbeat가 나온다."""
    other = _occupancy_event(event_id="other-event", classroom_id="classroom-other")
    _, gen = _stream(service, broadcaster, settings)

    async def _run() -> str:
        await anext(gen)  # retry
        broadcaster.publish(other)
        # 필터링된 뒤 heartbeat 간격(1초)이 지나면 heartbeat가 나온다.
        heartbeat = await anext(gen)
        await gen.aclose()
        return heartbeat

    assert asyncio.run(_run()) == ": heartbeat\n\n"


def test_subscriber_removed_after_close(
    service: ClassroomService, broadcaster: InMemoryBroadcaster, settings: Settings
) -> None:
    """연결이 끊기면(제너레이터 닫힘) broadcaster 구독자 목록에서 queue가 제거된다."""
    _, gen = _stream(service, broadcaster, settings)

    async def _run() -> bool:
        await anext(gen)  # retry
        subscribed = len(broadcaster._subscribers) == 1
        await gen.aclose()
        return subscribed

    assert asyncio.run(_run()) is True
    assert broadcaster._subscribers == []
