"""카메라 source 원장과 재생 세션. 결정론적이며 외부 호출이 없다."""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo

from ..classrooms.errors import ClassroomNotFoundError
from ..classrooms.service import ClassroomService
from .errors import (
    PlaybackSessionExpiredError,
    PlaybackSessionNotFoundError,
    PlaybackSessionOwnerMismatchError,
    PlaybackSessionStateInvalidError,
    PlaybackSourceUnavailableError,
    PlaybackStreamNotFoundError,
    VideoStreamAlreadyExistsError,
    VideoStreamNotFoundError,
    WhepTimeoutError,
    WhepUnavailableError,
)
from .models import (
    CameraRole,
    PlaybackKind,
    PlaybackSession,
    PlaybackSessionCreateResult,
    PlaybackSessionStatus,
    SourceStatus,
    VideoStream,
)
from .ports import PlaybackSessionRepository, VideoStreamRepository, WhepClient

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")
_TOKEN_PATTERN = re.compile(r"[0-9a-z\uac00-\ud7a3]+")
_HOUR_PATTERN = re.compile(
    r"(?P<hour>\d{1,2})\s*\uc2dc\s*(?P<direction>\uc774\ud6c4|\uc804|\uc774\uc804)"
)
_GENERIC_TERMS = frozenset(
    {
        "\uc601\uc0c1",
        "\ub370\ubaa8",
        "\uac80\uc0c9",
        "\ucc3e\uc544\uc8fc",
        "\ubcf4\uc5ec\uc8fc",
        "\uc788\ub358",
        "\uc788\ub294",
        "\uac15\uc758\uc2e4",
        "\uc5d0\uc11c",
        "\uc5b4\uc81c",
        "\uc624\ub298",
        "\uc774\ud6c4",
        "\uc774\uc804",
    }
)
_SEMANTIC_ALIASES: dict[str, tuple[str, ...]] = {
    "\uc778\uc6d0 \uc794\ub954": ("\uc0ac\ub78c", "\uc778\uc6d0", "\ub0a8\uc544", "\uc794\ub954"),
    "\ub9c8\uac10 \ud6c4": ("\ub9c8\uac10", "\uc5c5\ubb34 \uc678", "\ubc29\uacfc \ud6c4"),
    "\uc88c\uc11d \uc810\uc720": ("\uc88c\uc11d", "\uc810\uc720", "\uc0ac\uc6a9 \uc911"),
    "\uc785\uc2e4": ("\uc785\uc2e4", "\ub4e4\uc5b4", "\uc785\uc7a5"),
    "\uc774\ub3d9": ("\uc774\ub3d9", "\uc6b4\ub3d9\uc785", "\uc6b4\ub3d9\ud558\ub294"),
    "\uc7a5\ube44 \uad6c\uc5ed": ("\uc7a5\ube44", "\uc2e4\uc2b5"),
    "\ube44\uc5b4 \uc788\uc74c": (
        "\ube44\uc5b4",
        "\ube48 \uacf5\uac04",
        "\uc544\ubb34\ub3c4 \uc5c6",
    ),
    "\uc6b4\ub3d9\uc785 \uc5c6\uc74c": ("\uc6b4\ub3d9\uc785 \uc5c6", "\uc815\uc9c0"),
}


class VideoStreamService:
    """Real video stream service."""

    def __init__(
        self,
        repository: VideoStreamRepository,
        classroom_service: ClassroomService,
        stale_seconds: int,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._classroom_service = classroom_service
        self._stale_seconds = stale_seconds
        self._clock = clock

    def register_stream(
        self,
        *,
        camera_id: str,
        classroom_id: str,
        camera_label: str,
        enabled: bool,
        role: CameraRole = CameraRole.SEAT_JUDGING,
    ) -> VideoStream:
        """실제 카메라 source를 새로 등록한다.

        MongoDB mode에는 시드가 돌지 않으므로(demo seed는 memory 전용) 이 경로가
        camera_id를 원장에 넣는 유일한 수단이다. 등록되지 않은 camera_id로 탐지
        이벤트가 오면 student_monitoring이 VideoStreamNotFoundError로 거절한다.

        재생 경로는 camera_id로만 조립한다. 호출자가 준 문자열을 그대로 쓰면
        WHEP proxy 대상이 외부 입력이 된다.
        """
        if self._repository.find_by_camera_id(camera_id) is not None:
            raise VideoStreamAlreadyExistsError()

        now = self._clock()
        return self.save_stream(
            VideoStream(
                id=str(uuid4()),
                camera_id=camera_id,
                classroom_id=classroom_id,
                camera_label=camera_label,
                playback_kind=PlaybackKind.WEBRTC,
                playback_path=f"/webrtc/{camera_id}",
                enabled=enabled,
                last_frame_at=None,
                last_detection_at=None,
                is_demo=False,
                created_at=now,
                updated_at=now,
                role=role,
            )
        )

    def save_stream(self, stream: VideoStream) -> VideoStream:
        """활성 실제 stream이 존재하는 활성 강의실만 참조하게 저장한다."""
        if stream.enabled and not stream.is_demo:
            self._classroom_service.get_classroom(stream.classroom_id)
        return self._repository.save(stream)

    def list_invalid_classroom_references(self) -> list[VideoStream]:
        """이미 저장된 활성 실제 stream 중 깨진 강의실 참조를 찾는다.

        기존 운영 데이터를 자동으로 고치지 않는다. startup에서 이 결과를 기록해
        관리자가 정확한 대상 강의실을 선택할 수 있게 한다.
        """
        invalid: list[VideoStream] = []
        for stream in self._repository.find_monitoring_streams():
            try:
                self._classroom_service.get_classroom(stream.classroom_id)
            except ClassroomNotFoundError:
                invalid.append(stream)
        return invalid

    def list_streams(self) -> list[VideoStream]:
        """List all enabled streams."""
        return self._repository.find_all_enabled()

    def list_monitoring_streams(self) -> list[VideoStream]:
        """모니터링 화면용 실제 영상 source를 반환한다.

        real-only 정책(enabled=true AND is_demo=false)의 소유자는 이 service와
        repository port/adapter다. template/router는 이 결과만 렌더링한다(MON-002).
        """
        return self._repository.find_monitoring_streams()

    def get_stream(self, stream_id: str) -> VideoStream:
        """source 식별자로 stream을 찾는다. id를 먼저 보고 camera_id로 fallback한다.

        목록이 돌려주는 `id`로 상세를 다시 조회하는 것이 기본 사용 흐름이다.
        기존 호출자가 쓰던 `camera_id`도 계속 받는다 — 같은 경로의 detections·
        detection-events·playback-sessions가 이미 이 두 단계 조회를 쓰고 있어
        하위 경로끼리 식별자 규칙이 갈리지 않게 맞춘다.
        """
        stream = self._repository.find_by_id(stream_id) or self._repository.find_by_camera_id(
            stream_id
        )
        if stream is None:
            raise VideoStreamNotFoundError()
        return stream

    def get_source_status(self, stream: VideoStream) -> SourceStatus:
        """Calculate source status based on last detection time."""
        if stream.last_detection_at is None:
            return SourceStatus.UNKNOWN

        now = self._clock().astimezone(UTC)
        elapsed = (now - stream.last_detection_at).total_seconds()

        if elapsed < self._stale_seconds:
            return SourceStatus.CONNECTED
        elif elapsed < self._stale_seconds * 2:
            return SourceStatus.STALE
        else:
            return SourceStatus.NO_VIDEO


class PlaybackSessionService:
    """결정 0014의 재생 세션 수명주기와 WHEP proxy 대행.

    상태 전이: CREATED -> ACTIVE -> CLOSED 또는 CREATED/ACTIVE -> EXPIRED.
    - PATCH(재협상)는 ACTIVE에서만, DELETE는 ACTIVE/CLOSED에서 idempotent.
    - 생성/활성화 실패는 세션을 ACTIVE로 만들지 않는다.
    - proxy target은 source의 camera_id와 deployment config로만 조립한다(SSRF 차단).
    """

    def __init__(
        self,
        *,
        session_repository: PlaybackSessionRepository,
        stream_repository: VideoStreamRepository,
        whep_client: WhepClient,
        whep_base_url: str,
        ttl_seconds: int,
        clock: Callable[[], datetime],
    ) -> None:
        self._session_repository = session_repository
        self._stream_repository = stream_repository
        self._whep_client = whep_client
        self._whep_base_url = whep_base_url.rstrip("/")
        self._ttl_seconds = ttl_seconds
        self._clock = clock

    def create_session(self, stream_id: str) -> PlaybackSessionCreateResult:
        """실제·enabled·WebRTC source만 재생 세션으로 만든다."""
        stream = self._stream_repository.find_by_id(
            stream_id
        ) or self._stream_repository.find_by_camera_id(stream_id)
        if stream is None:
            raise PlaybackStreamNotFoundError()
        if not self._is_playable(stream):
            raise PlaybackSourceUnavailableError()

        now = self._clock().astimezone(UTC)
        session_id = secrets.token_hex(24)
        owner_token = secrets.token_urlsafe(32)
        session = PlaybackSession(
            session_id=session_id,
            stream_id=stream.id,
            camera_id=stream.camera_id,
            status=PlaybackSessionStatus.CREATED,
            owner_token_hash=self._hash_owner_token(owner_token),
            expires_at=now + timedelta(seconds=self._ttl_seconds),
            remote_resource_location=None,
            created_at=now,
            updated_at=now,
        )
        self._session_repository.save(session)
        return PlaybackSessionCreateResult(session=session, owner_token=owner_token)

    def activate(
        self,
        *,
        session_id: str,
        stream_id: str,
        owner_token: str | None,
        offer_sdp: str,
    ) -> str:
        """WHEP offer를 MediaMTX에 대행하고 answer를 받는다.

        remote POST가 성공하고 resource location을 서버 쪽에서 보관해야 ACTIVE가
        된다. 실패하면 세션은 CREATED에 남는다.
        """
        session = self._find_live_session(session_id, stream_id, owner_token)
        if session.status != PlaybackSessionStatus.CREATED:
            raise PlaybackSessionStateInvalidError()

        stream = self._stream_repository.find_by_id(
            session.stream_id
        ) or self._stream_repository.find_by_camera_id(session.stream_id)
        if stream is None or not self._is_playable(stream):
            raise PlaybackSourceUnavailableError()

        target_url = self._assemble_whep_target(stream.camera_id)
        result = self._whep_client.post_offer(target_url, offer_sdp)
        resource_location = self._resolve_resource_location(result.resource_location, target_url)
        self._session_repository.save(
            replace(
                session,
                status=PlaybackSessionStatus.ACTIVE,
                remote_resource_location=resource_location,
                updated_at=self._clock().astimezone(UTC),
            )
        )
        return result.answer_sdp

    def renegotiate(
        self,
        *,
        session_id: str,
        stream_id: str,
        owner_token: str | None,
        offer_sdp: str,
    ) -> str:
        """ACTIVE 세션의 재협상(WHEP PATCH)만 대행한다."""
        session = self._find_live_session(session_id, stream_id, owner_token)
        if session.status != PlaybackSessionStatus.ACTIVE:
            raise PlaybackSessionStateInvalidError()
        location = session.remote_resource_location
        if location is None:
            raise PlaybackSessionStateInvalidError()
        return self._whep_client.patch_offer(location, offer_sdp)

    def close(
        self,
        *,
        session_id: str,
        stream_id: str,
        owner_token: str | None,
    ) -> None:
        """WHEP resource를 닫고 세션을 CLOSED로 만든다. CLOSED에서는 idempotent."""
        session = self._find_live_session(session_id, stream_id, owner_token)
        if session.status == PlaybackSessionStatus.CLOSED:
            return
        if session.status != PlaybackSessionStatus.ACTIVE:
            raise PlaybackSessionStateInvalidError()

        now = self._clock().astimezone(UTC)
        self._session_repository.save(
            replace(session, status=PlaybackSessionStatus.CLOSED, updated_at=now)
        )
        location = session.remote_resource_location
        if location is None:
            return
        try:
            self._whep_client.delete(location)
        except (WhepUnavailableError, WhepTimeoutError):
            # remote cleanup 실패는 log 대상이다. local session은 CLOSED를 유지한다.
            logger.warning(
                "playback session remote cleanup failed session_id=%s",
                session.session_id,
                exc_info=True,
            )

    @staticmethod
    def _hash_owner_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _find_live_session(
        self,
        session_id: str,
        stream_id: str,
        owner_token: str | None,
    ) -> PlaybackSession:
        session = self._session_repository.find_by_id(session_id)
        if session is None or session.stream_id != stream_id:
            raise PlaybackSessionNotFoundError()
        if owner_token is None or not hmac.compare_digest(
            self._hash_owner_token(owner_token), session.owner_token_hash
        ):
            raise PlaybackSessionOwnerMismatchError()
        if self._clock().astimezone(UTC) > session.expires_at:
            self._expire_session(session)
            raise PlaybackSessionExpiredError()
        return session

    def _expire_session(self, session: PlaybackSession) -> None:
        """TTL 만료: EXPIRED로 저장하고 remote 정리를 한 번 시도한다.

        remote 정리 실패는 log 대상이며, 세션을 다시 활성화하지 않는다.
        """
        now = self._clock().astimezone(UTC)
        expired = replace(session, status=PlaybackSessionStatus.EXPIRED, updated_at=now)
        self._session_repository.save(expired)
        location = session.remote_resource_location
        if session.status == PlaybackSessionStatus.ACTIVE and location is not None:
            try:
                self._whep_client.delete(location)
            except (WhepUnavailableError, WhepTimeoutError):
                logger.warning(
                    "playback session expiry cleanup failed session_id=%s",
                    session.session_id,
                    exc_info=True,
                )

    def _assemble_whep_target(self, camera_id: str) -> str:
        """proxy target은 camera_id와 deployment config로만 조립한다(SSRF 차단)."""
        return f"{self._whep_base_url}/{camera_id}/whep"

    @staticmethod
    def _resolve_resource_location(location: str, target_url: str) -> str:
        """MediaMTX가 돌려준 Location을 같은 origin으로 한정해 절대 URL로 만든다."""
        if not location:
            raise WhepUnavailableError()
        parsed_target = urlparse(target_url)
        if parsed_target.scheme not in {"http", "https"} or not parsed_target.netloc:
            raise WhepUnavailableError()
        parsed_location = urlparse(location)
        if parsed_location.scheme:
            if (parsed_location.scheme, parsed_location.netloc) != (
                parsed_target.scheme,
                parsed_target.netloc,
            ):
                raise WhepUnavailableError()
            return location
        if not location.startswith("/"):
            raise WhepUnavailableError()
        return f"{parsed_target.scheme}://{parsed_target.netloc}{location}"

    @staticmethod
    def _is_playable(stream: VideoStream) -> bool:
        return stream.enabled and not stream.is_demo and stream.playback_kind == PlaybackKind.WEBRTC
