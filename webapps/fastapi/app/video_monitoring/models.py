"""Immutable values for camera sources, playback sessions, and their status."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class PlaybackKind(StrEnum):
    WEBRTC = "WEBRTC"
    UNAVAILABLE = "UNAVAILABLE"


class CameraRole(StrEnum):
    """카메라가 판정에 참여하는 범위 (결정 0024의 3번).

    장비 구성이 전체 조망 CCTV 1대 + 입구 카메라 1대로 바뀌면서, 두 카메라가 하는 일이
    갈렸다. 역할을 데이터로 두지 않으면 좌석 ROI가 없는 입구 카메라의 이벤트가 "최신"
    이라는 이유만으로 직전 판정을 UNKNOWN으로 덮는다 — 결정 0020이 남은 일로 적어 둔
    문제다.

    `IDENTITY_ONLY` 카메라의 탐지는 좌석 점유와 좌석 대조에 참여하지 않는다. worker가
    입구 신원을 교실 CCTV ByteTrack으로 인계하고, 좌석 판정은 `SEAT_JUDGING` 카메라의
    보강된 detection만 사용한다(결정 0036).
    """

    SEAT_JUDGING = "SEAT_JUDGING"  # 좌석 판정을 한다 (조망 CCTV)
    IDENTITY_ONLY = "IDENTITY_ONLY"  # 신원만 만든다 (입구 카메라)


class SourceStatus(StrEnum):
    CONNECTED = "CONNECTED"
    STALE = "STALE"
    NO_VIDEO = "NO_VIDEO"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class VideoStream:
    """Real camera source metadata."""

    id: str
    camera_id: str
    classroom_id: str
    camera_label: str
    playback_kind: PlaybackKind
    playback_path: str | None
    enabled: bool
    last_frame_at: datetime | None
    last_detection_at: datetime | None
    is_demo: bool
    created_at: datetime
    updated_at: datetime
    # 기본값은 좌석 판정이다. 기존 카메라의 동작을 바꾸지 않기 위해서이며,
    # 입구 카메라를 붙일 때만 명시적으로 IDENTITY_ONLY로 등록한다.
    role: CameraRole = CameraRole.SEAT_JUDGING


class PlaybackSessionStatus(StrEnum):
    """Playback session lifecycle (결정 0014).

    CREATED -> ACTIVE -> CLOSED 이거나 CREATED/ACTIVE -> EXPIRED다.
    """

    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class PlaybackSession:
    """결정 0014의 짧은 수명 재생 세션.

    owner_token_hash는 HttpOnly owner cookie 값의 SHA-256 해시를 보관한다.
    원문 토큰은 생성 응답에서만 한 번 노출되고 저장소에 남지 않는다.
    remote_resource_location은 MediaMTX가 돌려준 WHEP resource를 가리키며,
    서비스가 같은 origin 검증을 마친 값만 보관한다.
    """

    session_id: str
    stream_id: str
    camera_id: str
    status: PlaybackSessionStatus
    owner_token_hash: str
    expires_at: datetime
    remote_resource_location: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class PlaybackSessionCreateResult:
    """세션 생성 결과. owner_token은 cookie 설정을 위해 라우터가 한 번만 받는다."""

    session: PlaybackSession
    owner_token: str
