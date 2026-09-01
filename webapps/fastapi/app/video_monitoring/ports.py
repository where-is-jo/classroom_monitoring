"""Video monitoring repository ports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .models import PlaybackSession, VideoStream


class VideoStreamRepository(Protocol):
    """Video stream repository port."""

    def find_by_id(self, stream_id: str) -> VideoStream | None:
        """Find stream by ID."""
        ...

    def find_by_camera_id(self, camera_id: str) -> VideoStream | None:
        """Find stream by camera ID."""
        ...

    def find_all_enabled(self) -> list[VideoStream]:
        """Find all enabled streams."""
        ...

    def find_monitoring_streams(self) -> list[VideoStream]:
        """Find real monitoring streams (enabled=true AND is_demo=false)."""
        ...

    def update_last_detection(self, camera_id: str, captured_at: datetime) -> None:
        """더 최신인 경우에만 마지막 탐지 시각을 갱신한다."""
        ...

    def save(self, stream: VideoStream) -> VideoStream:
        """Save stream (seed and admin registration)."""
        ...


class PlaybackSessionRepository(Protocol):
    """결정 0014의 재생 세션 저장소 포트.

    TTL cleanup은 접근 시 lazy로 처리한다(만료 탐지 -> EXPIRED 저장 -> remote 정리).
    """

    def save(self, session: PlaybackSession) -> PlaybackSession:
        """세션을 저장한다. 같은 session_id면 교체한다."""
        ...

    def find_by_id(self, session_id: str) -> PlaybackSession | None:
        """session_id로 세션을 찾는다."""
        ...

    def delete_by_id(self, session_id: str) -> bool:
        """세션을 삭제하고, 존재했으면 True를 반환한다."""
        ...


@dataclass(frozen=True)
class WhepPostResult:
    """MediaMTX WHEP POST 응답. answer SDP와 resource location을 담는다."""

    answer_sdp: str
    resource_location: str


class WhepClient(Protocol):
    """MediaMTX WHEP signaling 클라이언트 포트.

    target URL은 항상 서버 쪽에서 조립·검증된 값만 받는다(SSRF 차단, 결정 0014).
    실패는 WhepUnavailableError(503) 또는 WhepTimeoutError(504)로 표현한다.
    """

    def post_offer(self, target_url: str, sdp: str) -> WhepPostResult:
        """새 WHEP offer를 보내 answer와 resource location을 받는다."""
        ...

    def patch_offer(self, resource_url: str, sdp: str) -> str:
        """재협상 offer를 보내 answer를 받는다."""
        ...

    def delete(self, resource_url: str) -> None:
        """WHEP resource를 닫는다."""
        ...
