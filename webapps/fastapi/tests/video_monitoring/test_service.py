"""video_monitoring 서비스 계층 단위 테스트 (real-only 필터링, 재생 세션)."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from app.classrooms.errors import ClassroomNotFoundError
from app.video_monitoring.adapters.memory_playback_session_repository import (
    MemoryPlaybackSessionRepository,
)
from app.video_monitoring.adapters.memory_repository import MemoryVideoStreamRepository
from app.video_monitoring.errors import (
    PlaybackSessionExpiredError,
    PlaybackSessionNotFoundError,
    PlaybackSessionOwnerMismatchError,
    PlaybackSessionStateInvalidError,
    PlaybackSourceUnavailableError,
    PlaybackStreamNotFoundError,
    WhepUnavailableError,
)
from app.video_monitoring.models import (
    PlaybackKind,
    PlaybackSessionStatus,
    VideoStream,
)
from app.video_monitoring.ports import WhepPostResult
from app.video_monitoring.service import PlaybackSessionService, VideoStreamService

from .fakes import (
    ANSWER_SDP,
    NOW,
    WHEP_BASE_URL,
    FakeClock,
    FakeWhepClient,
    make_classroom_service,
    make_stream,
)


def build_service(
    *,
    streams: list[VideoStream] | None = None,
    whep: FakeWhepClient | None = None,
    clock: FakeClock | None = None,
) -> tuple[
    PlaybackSessionService,
    MemoryPlaybackSessionRepository,
    MemoryVideoStreamRepository,
    FakeWhepClient,
    FakeClock,
]:
    stream_repository = MemoryVideoStreamRepository()
    for stream in streams or [make_stream()]:
        stream_repository.save(stream)
    session_repository = MemoryPlaybackSessionRepository()
    fake_whep = whep or FakeWhepClient()
    fake_clock = clock or FakeClock()
    service = PlaybackSessionService(
        session_repository=session_repository,
        stream_repository=stream_repository,
        whep_client=fake_whep,
        whep_base_url=WHEP_BASE_URL,
        ttl_seconds=300,
        clock=fake_clock,
    )
    return service, session_repository, stream_repository, fake_whep, fake_clock


# ── list_monitoring_streams (MON-002) ────────────────────────────────────────


def test_list_monitoring_streams_returns_only_enabled_real_streams() -> None:
    repository = MemoryVideoStreamRepository()
    for stream in (
        make_stream(stream_id="stream-01", camera_id="camera-01"),
        make_stream(stream_id="stream-02", camera_id="camera-02", is_demo=True),
        make_stream(stream_id="stream-03", camera_id="camera-03", enabled=False),
        make_stream(
            stream_id="stream-04",
            camera_id="camera-04",
            enabled=False,
            is_demo=True,
        ),
    ):
        repository.save(stream)
    service = VideoStreamService(
        repository,
        make_classroom_service(),
        stale_seconds=300,
        clock=FakeClock(),
    )

    assert [item.camera_id for item in service.list_monitoring_streams()] == ["camera-01"]


def test_list_monitoring_streams_keeps_existing_all_enabled_contract() -> None:
    """기존 list_streams(enabled만)는 demo를 포함한 채로 유지된다 (MON-007)."""
    repository = MemoryVideoStreamRepository()
    for stream in (
        make_stream(stream_id="stream-01", camera_id="camera-01"),
        make_stream(stream_id="stream-02", camera_id="camera-02", is_demo=True),
        make_stream(stream_id="stream-03", camera_id="camera-03", enabled=False),
    ):
        repository.save(stream)
    service = VideoStreamService(
        repository,
        make_classroom_service(),
        stale_seconds=300,
        clock=FakeClock(),
    )

    assert {item.camera_id for item in service.list_streams()} == {
        "camera-01",
        "camera-02",
    }


def test_save_stream_accepts_active_classroom_reference() -> None:
    repository = MemoryVideoStreamRepository()
    service = VideoStreamService(
        repository,
        make_classroom_service(),
        stale_seconds=300,
        clock=FakeClock(),
    )

    saved = service.save_stream(make_stream())

    assert repository.find_by_camera_id(saved.camera_id) == saved


def test_save_stream_rejects_missing_classroom_reference() -> None:
    repository = MemoryVideoStreamRepository()
    service = VideoStreamService(
        repository,
        make_classroom_service(),
        stale_seconds=300,
        clock=FakeClock(),
    )
    stream = replace(make_stream(), classroom_id="classroom-missing")

    with pytest.raises(ClassroomNotFoundError):
        service.save_stream(stream)

    assert repository.find_by_camera_id(stream.camera_id) is None


def test_save_stream_rejects_inactive_classroom_reference() -> None:
    repository = MemoryVideoStreamRepository()
    service = VideoStreamService(
        repository,
        make_classroom_service(active=False),
        stale_seconds=300,
        clock=FakeClock(),
    )
    stream = make_stream()

    with pytest.raises(ClassroomNotFoundError):
        service.save_stream(stream)

    assert repository.find_by_camera_id(stream.camera_id) is None


def test_list_invalid_classroom_references_reports_persisted_legacy_stream() -> None:
    repository = MemoryVideoStreamRepository()
    valid = make_stream(stream_id="stream-valid", camera_id="camera-valid")
    invalid = replace(
        make_stream(stream_id="stream-invalid", camera_id="camera-invalid"),
        classroom_id="classroom-missing",
    )
    repository.save(valid)
    repository.save(invalid)
    service = VideoStreamService(
        repository,
        make_classroom_service(),
        stale_seconds=300,
        clock=FakeClock(),
    )

    assert service.list_invalid_classroom_references() == [invalid]


# ── create_session ────────────────────────────────────────────────────────────


def test_create_session_success() -> None:
    service, session_repository, _, _, _ = build_service()

    created = service.create_session("stream-01")

    assert created.owner_token
    session = created.session
    assert session.status == PlaybackSessionStatus.CREATED
    assert session.stream_id == "stream-01"
    assert session.camera_id == "camera-01"
    assert session.expires_at == NOW + timedelta(seconds=300)
    assert session.remote_resource_location is None
    assert session.owner_token_hash != created.owner_token
    assert session_repository.find_by_id(session.session_id) == session


def test_create_session_accepts_camera_id_lookup() -> None:
    """stream_id로도, camera_id로도 source를 찾을 수 있어야 한다."""
    service, _, _, _, _ = build_service()

    by_id = service.create_session("stream-01")
    by_camera_id = service.create_session("camera-01")

    assert by_id.session.session_id != by_camera_id.session.session_id


def test_create_session_missing_stream_raises_not_found() -> None:
    service, _, _, _, _ = build_service()

    with pytest.raises(PlaybackStreamNotFoundError):
        service.create_session("stream-missing")


def test_create_session_rejects_demo_stream() -> None:
    service, _, _, _, _ = build_service(
        streams=[make_stream(stream_id="stream-demo", camera_id="camera-demo", is_demo=True)]
    )

    with pytest.raises(PlaybackSourceUnavailableError):
        service.create_session("stream-demo")


def test_create_session_rejects_disabled_stream() -> None:
    service, _, _, _, _ = build_service(
        streams=[make_stream(stream_id="stream-off", camera_id="camera-off", enabled=False)]
    )

    with pytest.raises(PlaybackSourceUnavailableError):
        service.create_session("stream-off")


def test_create_session_rejects_non_webrtc_stream() -> None:
    service, _, _, _, _ = build_service(
        streams=[
            make_stream(
                stream_id="stream-no-video",
                camera_id="camera-no-video",
                playback_kind=PlaybackKind.UNAVAILABLE,
            )
        ]
    )

    with pytest.raises(PlaybackSourceUnavailableError):
        service.create_session("stream-no-video")


# ── activate (WHEP offer POST) ────────────────────────────────────────────────


def _create_and_get(
    service: PlaybackSessionService, stream_id: str = "stream-01"
) -> tuple[str, str]:
    created = service.create_session(stream_id)
    return created.session.session_id, created.owner_token


def test_activate_success_proxies_and_becomes_active() -> None:
    service, session_repository, _, whep, _ = build_service()
    session_id, owner_token = _create_and_get(service)

    answer = service.activate(
        session_id=session_id,
        stream_id="stream-01",
        owner_token=owner_token,
        offer_sdp="v=0\r\noffer",
    )

    assert answer == ANSWER_SDP
    assert whep.posted == [(f"{WHEP_BASE_URL}/camera-01/whep", "v=0\r\noffer")]
    session = session_repository.find_by_id(session_id)
    assert session is not None
    assert session.status == PlaybackSessionStatus.ACTIVE
    assert session.remote_resource_location == (f"{WHEP_BASE_URL}/webrtc/camera-01/whep")


def test_activate_resolves_absolute_resource_location_same_origin() -> None:
    service, session_repository, _, whep, _ = build_service()
    session_id, owner_token = _create_and_get(service)
    whep.post_result = WhepPostResult(
        answer_sdp=ANSWER_SDP,
        resource_location=f"{WHEP_BASE_URL}/webrtc/camera-01/whep",
    )

    service.activate(
        session_id=session_id,
        stream_id="stream-01",
        owner_token=owner_token,
        offer_sdp="v=0\r\noffer",
    )

    session = session_repository.find_by_id(session_id)
    assert session is not None
    assert session.remote_resource_location == f"{WHEP_BASE_URL}/webrtc/camera-01/whep"


def test_activate_rejects_foreign_resource_location() -> None:
    """MediaMTX가 다른 origin Location을 돌려줘도 보관하지 않는다 (SSRF 차단)."""
    service, session_repository, _, whep, _ = build_service()
    session_id, owner_token = _create_and_get(service)
    whep.post_result = WhepPostResult(
        answer_sdp=ANSWER_SDP,
        resource_location="http://evil.example/webrtc/camera-01/whep",
    )

    with pytest.raises(WhepUnavailableError):
        service.activate(
            session_id=session_id,
            stream_id="stream-01",
            owner_token=owner_token,
            offer_sdp="v=0\r\noffer",
        )

    session = session_repository.find_by_id(session_id)
    assert session is not None
    assert session.status == PlaybackSessionStatus.CREATED
    assert session.remote_resource_location is None


def test_activate_rejects_empty_resource_location() -> None:
    service, session_repository, _, whep, _ = build_service()
    session_id, owner_token = _create_and_get(service)
    whep.post_result = WhepPostResult(answer_sdp=ANSWER_SDP, resource_location="")

    with pytest.raises(WhepUnavailableError):
        service.activate(
            session_id=session_id,
            stream_id="stream-01",
            owner_token=owner_token,
            offer_sdp="v=0\r\noffer",
        )

    session = session_repository.find_by_id(session_id)
    assert session is not None
    assert session.status == PlaybackSessionStatus.CREATED


def test_activate_failure_keeps_session_created() -> None:
    """생성/POST 실패는 세션을 ACTIVE로 만들지 않는다 (결정 0014 #6)."""
    service, session_repository, _, whep, _ = build_service()
    session_id, owner_token = _create_and_get(service)
    whep.post_error = WhepUnavailableError()

    with pytest.raises(WhepUnavailableError):
        service.activate(
            session_id=session_id,
            stream_id="stream-01",
            owner_token=owner_token,
            offer_sdp="v=0\r\noffer",
        )

    session = session_repository.find_by_id(session_id)
    assert session is not None
    assert session.status == PlaybackSessionStatus.CREATED


def test_activate_requires_created_state() -> None:
    service, _, _, _, _ = build_service()
    session_id, owner_token = _create_and_get(service)
    service.activate(
        session_id=session_id,
        stream_id="stream-01",
        owner_token=owner_token,
        offer_sdp="v=0\r\noffer",
    )

    with pytest.raises(PlaybackSessionStateInvalidError):
        service.activate(
            session_id=session_id,
            stream_id="stream-01",
            owner_token=owner_token,
            offer_sdp="v=0\r\noffer",
        )


def test_activate_owner_mismatch_is_forbidden() -> None:
    service, _, _, _, _ = build_service()
    session_id, _ = _create_and_get(service)

    with pytest.raises(PlaybackSessionOwnerMismatchError):
        service.activate(
            session_id=session_id,
            stream_id="stream-01",
            owner_token="wrong-token",
            offer_sdp="v=0\r\noffer",
        )


def test_activate_missing_cookie_is_forbidden() -> None:
    service, _, _, _, _ = build_service()
    session_id, _ = _create_and_get(service)

    with pytest.raises(PlaybackSessionOwnerMismatchError):
        service.activate(
            session_id=session_id,
            stream_id="stream-01",
            owner_token=None,
            offer_sdp="v=0\r\noffer",
        )


def test_activate_unknown_session_is_not_found() -> None:
    service, _, _, _, _ = build_service()

    with pytest.raises(PlaybackSessionNotFoundError):
        service.activate(
            session_id="unknown-session",
            stream_id="stream-01",
            owner_token="token",
            offer_sdp="v=0\r\noffer",
        )


def test_activate_stream_id_mismatch_is_not_found() -> None:
    service, _, _, _, _ = build_service()
    session_id, owner_token = _create_and_get(service)

    with pytest.raises(PlaybackSessionNotFoundError):
        service.activate(
            session_id=session_id,
            stream_id="stream-other",
            owner_token=owner_token,
            offer_sdp="v=0\r\noffer",
        )


def test_activate_expired_session_returns_gone_and_cleans_up() -> None:
    clock = FakeClock()
    service, session_repository, _, whep, _ = build_service(clock=clock)
    session_id, owner_token = _create_and_get(service)
    service.activate(
        session_id=session_id,
        stream_id="stream-01",
        owner_token=owner_token,
        offer_sdp="v=0\r\noffer",
    )
    whep.deleted.clear()
    clock.now = NOW + timedelta(seconds=301)

    with pytest.raises(PlaybackSessionExpiredError):
        service.activate(
            session_id=session_id,
            stream_id="stream-01",
            owner_token=owner_token,
            offer_sdp="v=0\r\noffer",
        )

    session = session_repository.find_by_id(session_id)
    assert session is not None
    assert session.status == PlaybackSessionStatus.EXPIRED
    assert whep.deleted == [f"{WHEP_BASE_URL}/webrtc/camera-01/whep"]


def test_activate_expired_cleanup_failure_is_ignored() -> None:
    """remote cleanup 실패는 log 대상이며 세션을 다시 활성화하지 않는다."""
    clock = FakeClock()
    service, session_repository, _, whep, _ = build_service(clock=clock)
    session_id, owner_token = _create_and_get(service)
    service.activate(
        session_id=session_id,
        stream_id="stream-01",
        owner_token=owner_token,
        offer_sdp="v=0\r\noffer",
    )
    whep.delete_error = WhepUnavailableError()
    clock.now = NOW + timedelta(seconds=301)

    with pytest.raises(PlaybackSessionExpiredError):
        service.activate(
            session_id=session_id,
            stream_id="stream-01",
            owner_token=owner_token,
            offer_sdp="v=0\r\noffer",
        )

    session = session_repository.find_by_id(session_id)
    assert session is not None
    assert session.status == PlaybackSessionStatus.EXPIRED


def test_activate_checks_source_playability_again() -> None:
    """source가 이후 비활성화되면 409를 돌린다 (결정 0014 #3)."""
    service, _, stream_repository, _, _ = build_service()
    session_id, owner_token = _create_and_get(service)
    stream = stream_repository.find_by_camera_id("camera-01")
    assert stream is not None
    stream_repository.save(make_stream(stream_id="stream-01", enabled=False))

    with pytest.raises(PlaybackSourceUnavailableError):
        service.activate(
            session_id=session_id,
            stream_id="stream-01",
            owner_token=owner_token,
            offer_sdp="v=0\r\noffer",
        )


# ── renegotiate (WHEP PATCH) ──────────────────────────────────────────────────


def test_renegotiate_success_on_active_session() -> None:
    service, _, _, whep, _ = build_service()
    session_id, owner_token = _create_and_get(service)
    service.activate(
        session_id=session_id,
        stream_id="stream-01",
        owner_token=owner_token,
        offer_sdp="v=0\r\noffer",
    )

    answer = service.renegotiate(
        session_id=session_id,
        stream_id="stream-01",
        owner_token=owner_token,
        offer_sdp="v=0\r\nre-offer",
    )

    assert answer == "v=0\r\nanswer-patch"
    assert whep.patched == [(f"{WHEP_BASE_URL}/webrtc/camera-01/whep", "v=0\r\nre-offer")]


def test_renegotiate_on_created_session_is_conflict() -> None:
    service, _, _, _, _ = build_service()
    session_id, owner_token = _create_and_get(service)

    with pytest.raises(PlaybackSessionStateInvalidError):
        service.renegotiate(
            session_id=session_id,
            stream_id="stream-01",
            owner_token=owner_token,
            offer_sdp="v=0\r\nre-offer",
        )


def test_renegotiate_on_expired_session_is_gone() -> None:
    clock = FakeClock()
    service, _, _, _, _ = build_service(clock=clock)
    session_id, owner_token = _create_and_get(service)
    clock.now = NOW + timedelta(seconds=301)

    with pytest.raises(PlaybackSessionExpiredError):
        service.renegotiate(
            session_id=session_id,
            stream_id="stream-01",
            owner_token=owner_token,
            offer_sdp="v=0\r\nre-offer",
        )


# ── close (WHEP DELETE) ───────────────────────────────────────────────────────


def test_close_active_session_deletes_remote_and_closes() -> None:
    service, session_repository, _, whep, _ = build_service()
    session_id, owner_token = _create_and_get(service)
    service.activate(
        session_id=session_id,
        stream_id="stream-01",
        owner_token=owner_token,
        offer_sdp="v=0\r\noffer",
    )

    service.close(session_id=session_id, stream_id="stream-01", owner_token=owner_token)

    assert whep.deleted == [f"{WHEP_BASE_URL}/webrtc/camera-01/whep"]
    session = session_repository.find_by_id(session_id)
    assert session is not None
    assert session.status == PlaybackSessionStatus.CLOSED


def test_close_is_idempotent_when_already_closed() -> None:
    service, _, _, whep, _ = build_service()
    session_id, owner_token = _create_and_get(service)
    service.activate(
        session_id=session_id,
        stream_id="stream-01",
        owner_token=owner_token,
        offer_sdp="v=0\r\noffer",
    )
    service.close(session_id=session_id, stream_id="stream-01", owner_token=owner_token)
    whep.deleted.clear()

    service.close(session_id=session_id, stream_id="stream-01", owner_token=owner_token)

    assert whep.deleted == []


def test_close_remote_failure_still_closes_locally() -> None:
    """remote delete 실패는 log 대상이지만 local state는 CLOSED를 유지한다."""
    service, session_repository, _, whep, _ = build_service()
    session_id, owner_token = _create_and_get(service)
    service.activate(
        session_id=session_id,
        stream_id="stream-01",
        owner_token=owner_token,
        offer_sdp="v=0\r\noffer",
    )
    whep.delete_error = WhepUnavailableError()

    service.close(session_id=session_id, stream_id="stream-01", owner_token=owner_token)

    session = session_repository.find_by_id(session_id)
    assert session is not None
    assert session.status == PlaybackSessionStatus.CLOSED


def test_close_on_created_session_is_conflict() -> None:
    service, _, _, _, _ = build_service()
    session_id, owner_token = _create_and_get(service)

    with pytest.raises(PlaybackSessionStateInvalidError):
        service.close(session_id=session_id, stream_id="stream-01", owner_token=owner_token)


def test_close_expired_session_is_gone() -> None:
    clock = FakeClock()
    service, _, _, _, _ = build_service(clock=clock)
    session_id, owner_token = _create_and_get(service)
    clock.now = NOW + timedelta(seconds=301)

    with pytest.raises(PlaybackSessionExpiredError):
        service.close(session_id=session_id, stream_id="stream-01", owner_token=owner_token)
