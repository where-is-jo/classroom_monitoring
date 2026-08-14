"""Domain errors for demo video queries."""

from __future__ import annotations

from ..shared.errors import DomainError


class DemoStreamNotFoundError(DomainError):
    code = "DEMO_STREAM_NOT_FOUND"
    status_code = 404

    def __init__(self) -> None:
        super().__init__("요청한 데모 피드를 찾을 수 없습니다.")


class VideoSearchInputError(DomainError):
    code = "VIDEO_SEARCH_INPUT_INVALID"
    status_code = 422

    def __init__(self, message: str = "영상 검색 조건이 올바르지 않습니다.") -> None:
        super().__init__(message)


class PlaybackStreamNotFoundError(DomainError):
    """재생 세션 대상 stream이 존재하지 않음."""

    code = "PLAYBACK_STREAM_NOT_FOUND"
    status_code = 404

    def __init__(self) -> None:
        super().__init__("요청한 영상 source를 찾을 수 없습니다.")


class PlaybackSourceUnavailableError(DomainError):
    """재생 불가 source (demo·비활성·비WebRTC)."""

    code = "PLAYBACK_SOURCE_UNAVAILABLE"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("이 영상 source는 재생할 수 없습니다.")


class PlaybackSessionNotFoundError(DomainError):
    """존재하지 않는 재생 세션."""

    code = "PLAYBACK_SESSION_NOT_FOUND"
    status_code = 404

    def __init__(self) -> None:
        super().__init__("요청한 재생 세션을 찾을 수 없습니다.")


class PlaybackSessionOwnerMismatchError(DomainError):
    """owner cookie가 session과 일치하지 않음."""

    code = "PLAYBACK_SESSION_OWNER_MISMATCH"
    status_code = 403

    def __init__(self) -> None:
        super().__init__("재생 세션 소유자만 요청할 수 있습니다.")


class PlaybackSessionExpiredError(DomainError):
    """TTL이 만료된 재생 세션."""

    code = "PLAYBACK_SESSION_EXPIRED"
    status_code = 410

    def __init__(self) -> None:
        super().__init__("재생 세션이 만료되었습니다.")


class PlaybackSessionStateInvalidError(DomainError):
    """현재 상태에서 허용되지 않는 전이 (PATCH on CREATED 등)."""

    code = "PLAYBACK_SESSION_STATE_INVALID"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("재생 세션 상태가 요청을 처리할 수 없습니다.")


class PlaybackSessionInputError(DomainError):
    """재생 세션 요청 본문 검증 실패."""

    code = "PLAYBACK_SESSION_INPUT_INVALID"
    status_code = 422

    def __init__(self, message: str = "재생 세션 요청이 올바르지 않습니다.") -> None:
        super().__init__(message)


class WhepUnavailableError(DomainError):
    """MediaMTX WHEP에 연결·처리 실패."""

    code = "WHEP_UNAVAILABLE"
    status_code = 503

    def __init__(self) -> None:
        super().__init__("영상 signaling 서버를 일시적으로 사용할 수 없습니다.")


class WhepTimeoutError(DomainError):
    """MediaMTX WHEP proxy timeout."""

    code = "WHEP_TIMEOUT"
    status_code = 504

    def __init__(self) -> None:
        super().__init__("영상 signaling 요청이 시간 초과되었습니다.")
