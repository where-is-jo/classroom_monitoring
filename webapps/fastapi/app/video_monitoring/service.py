"""Deterministic catalog filtering; no AI, persistence, or external calls."""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import unicodedata
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo

from ..classrooms.errors import ClassroomNotFoundError
from ..classrooms.service import ClassroomService
from .catalog import DEMO_STREAMS, DEMO_VIDEO_CLIPS
from .errors import (
    DemoStreamNotFoundError,
    PlaybackSessionExpiredError,
    PlaybackSessionNotFoundError,
    PlaybackSessionOwnerMismatchError,
    PlaybackSessionStateInvalidError,
    PlaybackSourceUnavailableError,
    PlaybackStreamNotFoundError,
    VideoSearchInputError,
    VideoStreamAlreadyExistsError,
    VideoStreamNotFoundError,
    WhepTimeoutError,
    WhepUnavailableError,
)
from .models import (
    DemoStream,
    DemoStreamStatus,
    DemoVideoClip,
    PlaybackKind,
    PlaybackSession,
    PlaybackSessionCreateResult,
    PlaybackSessionStatus,
    SourceStatus,
    VideoSearchResult,
    VideoSearchResultPage,
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


class VideoDemoService:
    def __init__(
        self,
        *,
        streams: tuple[DemoStream, ...] = DEMO_STREAMS,
        clips: tuple[DemoVideoClip, ...] = DEMO_VIDEO_CLIPS,
        clock: Callable[[], datetime],
    ) -> None:
        self._streams = streams
        self._clips = clips
        self._clock = clock

    def current_time(self) -> datetime:
        return self._clock().astimezone(UTC)

    def list_streams(
        self,
        *,
        search: str | None,
        classroom_id: str | None,
        status: DemoStreamStatus | None,
    ) -> list[DemoStream]:
        items = list(self._streams)
        if classroom_id:
            items = [item for item in items if item.classroom_id == classroom_id]
        if status is not None:
            items = [item for item in items if item.status == status]
        if search and (normalized := self._normalize(search)):
            items = [
                item
                for item in items
                if normalized
                in self._normalize(
                    f"{item.classroom_code} {item.classroom_name} {item.camera_label}"
                )
            ]
        return items

    def get_stream(self, stream_id: str) -> DemoStream:
        stream = next((item for item in self._streams if item.id == stream_id), None)
        if stream is None:
            raise DemoStreamNotFoundError()
        return stream

    def search_videos(
        self,
        query: str,
        *,
        classroom_id: str | None,
        from_at: datetime | None,
        to_at: datetime | None,
        limit: int,
    ) -> VideoSearchResultPage:
        normalized_query = self._normalize(query)
        if not normalized_query or len(query.strip()) > 200:
            raise VideoSearchInputError("Search query must be 1-200 characters.")
        from_at = self._aware_utc(from_at)
        to_at = self._aware_utc(to_at)
        if from_at is not None and to_at is not None and from_at > to_at:
            raise VideoSearchInputError("Start time must not be later than end time.")
        if limit < 1 or limit > 50:
            raise VideoSearchInputError("Result count must be 1-50.")

        concepts = self._semantic_concepts(normalized_query)
        raw_terms = self._raw_terms(normalized_query, concepts)
        query_date = self._query_date(normalized_query)
        hour_filter = self._hour_filter(normalized_query)
        matches: list[VideoSearchResult] = []
        for clip in self._clips:
            if classroom_id and clip.classroom_id != classroom_id:
                continue
            if from_at is not None and clip.ended_at < from_at:
                continue
            if to_at is not None and clip.started_at > to_at:
                continue
            local_start = clip.started_at.astimezone(_KST)
            if query_date is not None and local_start.date() != query_date:
                continue
            if hour_filter is not None:
                hour, after = hour_filter
                if (after and local_start.hour < hour) or (not after and local_start.hour >= hour):
                    continue
            haystack = self._normalize(
                " ".join(
                    (
                        clip.title,
                        clip.classroom_code,
                        clip.classroom_name,
                        clip.summary,
                        *clip.tags,
                    )
                )
            )
            if any(concept not in haystack for concept in concepts):
                continue
            if any(term not in haystack for term in raw_terms):
                continue
            matched = tuple(dict.fromkeys((*concepts, *raw_terms)))
            reason = (
                " \u00b7 ".join(matched)
                if matched
                else "\uc804\uccb4 \ub370\ubaa8 \uce90\ud0c8\ub85c\uadf8"
            )
            matches.append(
                VideoSearchResult(
                    clip=clip,
                    matched_terms=matched,
                    match_reason=f"\uace0\uc815 \uba54\ud0c0\ub370\uc774\ud130 \uc77c\uce58: {reason}",
                )
            )
        matches.sort(key=lambda item: (item.clip.started_at, item.clip.id), reverse=True)
        return VideoSearchResultPage(items=matches[:limit], total=len(matches), limit=limit)

    def classroom_options(self) -> list[tuple[str, str]]:
        return list(
            dict.fromkeys(
                (item.classroom_id, f"{item.classroom_code} {item.classroom_name}")
                for item in self._streams
            )
        )

    def _query_date(self, query: str) -> date | None:
        if not self._clips:
            return None
        reference = max(item.started_at.astimezone(_KST).date() for item in self._clips)
        if "\uc5b4\uc81c" in query:
            return reference
        if "\uc624\ub298" in query:
            return reference + timedelta(days=1)
        return None

    @staticmethod
    def _hour_filter(query: str) -> tuple[int, bool] | None:
        match = _HOUR_PATTERN.search(query)
        if match is None:
            return None
        hour = int(match.group("hour"))
        if hour < 0 or hour > 23:
            raise VideoSearchInputError("Hour must be 0-23.")
        if 1 <= hour <= 7:
            hour += 12
        return hour, match.group("direction") == "\uc774\ud6c4"

    @staticmethod
    def _semantic_concepts(query: str) -> tuple[str, ...]:
        return tuple(
            concept
            for concept, aliases in _SEMANTIC_ALIASES.items()
            if any(alias in query for alias in aliases)
        )

    @staticmethod
    def _raw_terms(query: str, concepts: tuple[str, ...]) -> tuple[str, ...]:
        covered_aliases = {
            alias
            for concept in concepts
            for alias in _SEMANTIC_ALIASES[concept]
            if " " not in alias
        }
        terms = []
        for token in _TOKEN_PATTERN.findall(query):
            token = VideoDemoService._strip_particle(token)
            if (
                len(token) < 2
                or token in _GENERIC_TERMS
                or token.isdigit()
                or bool(re.fullmatch(r"\d{1,2}\uc2dc", token))
                or any(alias in token or token in alias for alias in covered_aliases)
                or token.endswith(
                    ("\uc5d0\ub294", "\uc5d0\uc11c", "\uc73c\ub85c", "\uc774\ud6c4", "\uc774\uc804")
                )
            ):
                continue
            terms.append(token)
        return tuple(dict.fromkeys(terms))

    @staticmethod
    def _strip_particle(token: str) -> str:
        for suffix in (
            "\uc5d0\uc11c\ub294",
            "\uc73c\ub85c",
            "\uc5d0\uc11c",
            "\uc5d0\uac8c",
            "\ubd80\ud130",
            "\uae4c\uc9c0",
            "\uc5d0\ub294",
            "\uc5d0",
            "\uc758",
            "\uc744",
            "\ub97c",
            "\uc774",
            "\uac00",
            "\uc740",
            "\ub294",
        ):
            if token.endswith(suffix) and len(token) > len(suffix) + 1:
                return token[: -len(suffix)]
        return token

    @staticmethod
    def _aware_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise VideoSearchInputError("Search time must include timezone.")
        return value.astimezone(UTC)

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


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
