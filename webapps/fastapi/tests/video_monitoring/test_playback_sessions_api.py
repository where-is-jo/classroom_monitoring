"""재생 세션 HTTP 계약 테스트 (결정 0014, MON-005 백엔드)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.shared.config import Settings
from app.shared.dependencies import get_playback_session_service, get_settings
from app.video_monitoring.adapters.memory_playback_session_repository import (
    MemoryPlaybackSessionRepository,
)
from app.video_monitoring.adapters.memory_repository import MemoryVideoStreamRepository
from app.video_monitoring.errors import WhepUnavailableError
from app.video_monitoring.models import PlaybackSessionStatus
from app.video_monitoring.service import PlaybackSessionService

from .fakes import (
    NOW,
    WHEP_BASE_URL,
    FakeClock,
    FakeWhepClient,
    make_stream,
)

OFFER_SDP = "v=0\r\noffer"


@dataclass
class PlaybackContext:
    client: TestClient
    service: PlaybackSessionService
    whep: FakeWhepClient
    clock: FakeClock
    session_repository: MemoryPlaybackSessionRepository


@pytest.fixture
def playback_context() -> Iterator[PlaybackContext]:
    stream_repository = MemoryVideoStreamRepository()
    for stream in (
        make_stream(stream_id="stream-01", camera_id="camera-01"),
        make_stream(stream_id="stream-demo", camera_id="camera-demo", is_demo=True),
        make_stream(stream_id="stream-off", camera_id="camera-off", enabled=False),
    ):
        stream_repository.save(stream)
    session_repository = MemoryPlaybackSessionRepository()
    whep = FakeWhepClient()
    clock = FakeClock()
    service = PlaybackSessionService(
        session_repository=session_repository,
        stream_repository=stream_repository,
        whep_client=whep,
        whep_base_url=WHEP_BASE_URL,
        ttl_seconds=300,
        clock=clock,
    )
    # local/http 테스트에서 owner cookie가 전송되도록 Secure를 끈다 (ADR 남은 일).
    settings = Settings(
        app_env="local",
        database_mode="memory",
        playback_session_cookie_secure=False,
    )
    app.dependency_overrides[get_playback_session_service] = lambda: service
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        yield PlaybackContext(
            client=client,
            service=service,
            whep=whep,
            clock=clock,
            session_repository=session_repository,
        )
    app.dependency_overrides.clear()


def _create_session(context: PlaybackContext, stream_id: str = "stream-01") -> tuple[str, str]:
    response = context.client.post(f"/api/v1/video-streams/{stream_id}/playback-sessions")
    assert response.status_code == 201
    payload = response.json()
    return payload["session_id"], payload["signaling_url"]


# ── 생성 ──────────────────────────────────────────────────────────────────────


def test_create_session_success_contract(playback_context: PlaybackContext) -> None:
    response = playback_context.client.post("/api/v1/video-streams/stream-01/playback-sessions")

    assert response.status_code == 201
    payload = response.json()
    assert set(payload) == {"session_id", "signaling_url", "expires_at"}
    assert payload["session_id"]
    assert payload["signaling_url"] == (
        f"/api/v1/video-streams/stream-01/playback-sessions/{payload['session_id']}"
    )
    assert response.headers["location"] == payload["signaling_url"]


def test_create_session_sets_owner_cookie(playback_context: PlaybackContext) -> None:
    response = playback_context.client.post("/api/v1/video-streams/stream-01/playback-sessions")
    session_id = response.json()["session_id"]

    set_cookie = response.headers["set-cookie"]
    assert f"playback_owner_{session_id}=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=strict" in set_cookie
    assert "Secure" not in set_cookie  # secure=False인 테스트 설정


def test_create_session_media_mtx_is_not_exposed(
    playback_context: PlaybackContext,
) -> None:
    """생성 응답·cookie에 MediaMTX 정보가 없어야 한다 (결정 0014 #1, #3)."""
    response = playback_context.client.post("/api/v1/video-streams/stream-01/playback-sessions")

    for forbidden in ("8889", "mediamtx", "rtsp", "whep"):
        assert forbidden not in response.text.lower()


def test_create_session_rejects_demo_source(playback_context: PlaybackContext) -> None:
    response = playback_context.client.post("/api/v1/video-streams/stream-demo/playback-sessions")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PLAYBACK_SOURCE_UNAVAILABLE"


def test_create_session_rejects_disabled_source(
    playback_context: PlaybackContext,
) -> None:
    response = playback_context.client.post("/api/v1/video-streams/stream-off/playback-sessions")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PLAYBACK_SOURCE_UNAVAILABLE"


def test_create_session_missing_stream_is_not_found(
    playback_context: PlaybackContext,
) -> None:
    response = playback_context.client.post(
        "/api/v1/video-streams/stream-missing/playback-sessions"
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PLAYBACK_STREAM_NOT_FOUND"


# ── WHEP offer (POST) ─────────────────────────────────────────────────────────


def test_whep_offer_success_with_owner_cookie(
    playback_context: PlaybackContext,
) -> None:
    session_id, signaling_url = _create_session(playback_context)

    response = playback_context.client.post(
        signaling_url,
        content=OFFER_SDP,
        headers={"Content-Type": "application/sdp"},
    )

    assert response.status_code == 201
    assert response.headers["content-type"] == "application/sdp"
    assert response.text == "v=0\r\nanswer"
    session = playback_context.session_repository.find_by_id(session_id)
    assert session is not None
    assert session.status == PlaybackSessionStatus.ACTIVE


def test_whep_offer_without_cookie_is_forbidden(
    playback_context: PlaybackContext,
) -> None:
    session_id, signaling_url = _create_session(playback_context)
    playback_context.client.cookies.delete(f"playback_owner_{session_id}")

    response = playback_context.client.post(
        signaling_url,
        content=OFFER_SDP,
        headers={"Content-Type": "application/sdp"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PLAYBACK_SESSION_OWNER_MISMATCH"


def test_whep_offer_with_wrong_cookie_is_forbidden(
    playback_context: PlaybackContext,
) -> None:
    session_id, signaling_url = _create_session(playback_context)
    playback_context.client.cookies.set(f"playback_owner_{session_id}", "wrong-token")

    response = playback_context.client.post(
        signaling_url,
        content=OFFER_SDP,
        headers={"Content-Type": "application/sdp"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PLAYBACK_SESSION_OWNER_MISMATCH"


def test_whep_offer_unknown_session_is_not_found(
    playback_context: PlaybackContext,
) -> None:
    response = playback_context.client.post(
        "/api/v1/video-streams/stream-01/playback-sessions/unknown-session",
        content=OFFER_SDP,
        headers={"Content-Type": "application/sdp"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PLAYBACK_SESSION_NOT_FOUND"


def test_whep_offer_oversized_sdp_is_rejected(
    playback_context: PlaybackContext,
) -> None:
    _session_id, signaling_url = _create_session(playback_context)

    response = playback_context.client.post(
        signaling_url,
        content="v=0\r\n" + "a=extmap:1 x" * 200000,
        headers={"Content-Type": "application/sdp"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PLAYBACK_SESSION_INPUT_INVALID"


def test_whep_offer_media_mtx_failure_keeps_session_created(
    playback_context: PlaybackContext,
) -> None:
    session_id, signaling_url = _create_session(playback_context)
    playback_context.whep.post_error = WhepUnavailableError()

    response = playback_context.client.post(
        signaling_url,
        content=OFFER_SDP,
        headers={"Content-Type": "application/sdp"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "WHEP_UNAVAILABLE"
    session = playback_context.session_repository.find_by_id(session_id)
    assert session is not None
    assert session.status == PlaybackSessionStatus.CREATED


def test_whep_offer_cross_origin_resource_is_rejected(
    playback_context: PlaybackContext,
) -> None:
    """MediaMTX가 다른 origin Location을 돌려줘도 ACTIVE로 만들지 않는다."""
    session_id, signaling_url = _create_session(playback_context)

    # FakeWhepClient.post_result를 다른 origin으로 교체한다.
    from app.video_monitoring.ports import WhepPostResult

    playback_context.whep.post_result = WhepPostResult(
        answer_sdp="v=0\r\nanswer",
        resource_location="http://evil.example/webrtc/camera-01/whep",
    )

    response = playback_context.client.post(
        signaling_url,
        content=OFFER_SDP,
        headers={"Content-Type": "application/sdp"},
    )

    assert response.status_code == 503
    session = playback_context.session_repository.find_by_id(session_id)
    assert session is not None
    assert session.status == PlaybackSessionStatus.CREATED


# ── 재협상 (PATCH) ────────────────────────────────────────────────────────────


def test_whep_renegotiate_success_on_active(
    playback_context: PlaybackContext,
) -> None:
    _session_id, signaling_url = _create_session(playback_context)
    playback_context.client.post(
        signaling_url,
        content=OFFER_SDP,
        headers={"Content-Type": "application/sdp"},
    )

    response = playback_context.client.patch(
        signaling_url,
        content="v=0\r\nre-offer",
        headers={"Content-Type": "application/sdp"},
    )

    assert response.status_code == 200
    assert response.text == "v=0\r\nanswer-patch"


def test_whep_renegotiate_on_created_is_conflict(
    playback_context: PlaybackContext,
) -> None:
    _session_id, signaling_url = _create_session(playback_context)

    response = playback_context.client.patch(
        signaling_url,
        content=OFFER_SDP,
        headers={"Content-Type": "application/sdp"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PLAYBACK_SESSION_STATE_INVALID"


# ── 종료 (DELETE) ─────────────────────────────────────────────────────────────


def test_whep_close_active_session_is_no_content(
    playback_context: PlaybackContext,
) -> None:
    session_id, signaling_url = _create_session(playback_context)
    playback_context.client.post(
        signaling_url,
        content=OFFER_SDP,
        headers={"Content-Type": "application/sdp"},
    )

    response = playback_context.client.delete(signaling_url)

    assert response.status_code == 204
    assert f"playback_owner_{session_id}=" not in response.headers.get("set-cookie", "")
    session = playback_context.session_repository.find_by_id(session_id)
    assert session is not None
    assert session.status == PlaybackSessionStatus.CLOSED


def test_whep_close_is_idempotent(playback_context: PlaybackContext) -> None:
    _session_id, signaling_url = _create_session(playback_context)
    playback_context.client.post(
        signaling_url,
        content=OFFER_SDP,
        headers={"Content-Type": "application/sdp"},
    )

    first = playback_context.client.delete(signaling_url)
    second = playback_context.client.delete(signaling_url)

    assert first.status_code == 204
    assert second.status_code == 204


def test_whep_close_without_cookie_is_forbidden(
    playback_context: PlaybackContext,
) -> None:
    session_id, signaling_url = _create_session(playback_context)
    playback_context.client.cookies.delete(f"playback_owner_{session_id}")

    response = playback_context.client.delete(signaling_url)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PLAYBACK_SESSION_OWNER_MISMATCH"


# ── 만료 (TTL) ────────────────────────────────────────────────────────────────


def test_expired_session_returns_gone(playback_context: PlaybackContext) -> None:
    session_id, signaling_url = _create_session(playback_context)
    playback_context.clock.now = NOW + timedelta(seconds=301)

    response = playback_context.client.post(
        signaling_url,
        content=OFFER_SDP,
        headers={"Content-Type": "application/sdp"},
    )

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "PLAYBACK_SESSION_EXPIRED"
    session = playback_context.session_repository.find_by_id(session_id)
    assert session is not None
    assert session.status == PlaybackSessionStatus.EXPIRED


def test_expired_session_cleanup_calls_remote_delete(
    playback_context: PlaybackContext,
) -> None:
    _session_id, signaling_url = _create_session(playback_context)
    playback_context.client.post(
        signaling_url,
        content=OFFER_SDP,
        headers={"Content-Type": "application/sdp"},
    )
    playback_context.whep.deleted.clear()
    playback_context.clock.now = NOW + timedelta(seconds=301)

    response = playback_context.client.post(
        signaling_url,
        content=OFFER_SDP,
        headers={"Content-Type": "application/sdp"},
    )

    assert response.status_code == 410
    assert playback_context.whep.deleted == [f"{WHEP_BASE_URL}/webrtc/camera-01/whep"]
