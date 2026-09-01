"""HTTP schemas for the synthetic monitoring demo."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import (
    CameraRole,
    PlaybackSession,
    SourceStatus,
    VideoStream,
)


class RealStreamResponse(BaseModel):
    id: str
    camera_id: str
    classroom_id: str
    camera_label: str
    role: CameraRole
    status: str
    playback_kind: str
    playback_path: str | None
    last_frame_at: datetime | None
    last_detection_at: datetime | None
    is_demo: Literal[False] = False

    @classmethod
    def from_domain(cls, item: VideoStream, status: SourceStatus) -> RealStreamResponse:
        return cls(
            id=item.id,
            camera_id=item.camera_id,
            classroom_id=item.classroom_id,
            camera_label=item.camera_label,
            role=item.role,
            status=status.value,
            playback_kind=item.playback_kind.value,
            playback_path=item.playback_path,
            last_frame_at=item.last_frame_at,
            last_detection_at=item.last_detection_at,
        )


class VideoStreamCreateRequest(BaseModel):
    """실제 카메라 source 등록 요청.

    playback_path·id·시각은 받지 않는다. 재생 경로는 camera_id로만 조립해야
    임의 주소를 넣을 수 없고(결정 0014의 SSRF 차단과 같은 이유), 나머지는 서버가 만든다.
    playback_kind도 WEBRTC로 고정한다 — 재생할 수 없는 source를 등록할 이유가 없다.
    """

    model_config = ConfigDict(extra="forbid")

    # worker의 STREAM_SOURCES에 적는 식별자와 같아야 한다. 다르면 탐지 이벤트가
    # 이 source를 찾지 못해 404로 거절된다.
    camera_id: str = Field(..., min_length=1, max_length=64)
    classroom_id: str = Field(..., min_length=1)
    camera_label: str = Field(..., min_length=1, max_length=100)
    # 입구 카메라는 좌석 판정에서 제외해야 하므로 등록 시 역할을 명시할 수 있어야 한다.
    # 생략하면 기존 카메라와 호환되도록 좌석 판정 역할을 유지한다.
    role: CameraRole = CameraRole.SEAT_JUDGING
    enabled: bool = True


class StreamListResponse(BaseModel):
    items: list[RealStreamResponse]
    total: int


class PlaybackSessionCreateResponse(BaseModel):
    """재생 세션 생성 응답 (결정 0014).

    opaque session_id, FastAPI signaling URL, expires_at만 포함한다.
    MediaMTX 주소·포트·RTSP URL·자격 증명은 넣지 않는다.
    """

    session_id: str
    signaling_url: str
    expires_at: datetime

    @classmethod
    def from_domain(
        cls, session: PlaybackSession, signaling_url: str
    ) -> PlaybackSessionCreateResponse:
        return cls(
            session_id=session.session_id,
            signaling_url=signaling_url,
            expires_at=session.expires_at,
        )
