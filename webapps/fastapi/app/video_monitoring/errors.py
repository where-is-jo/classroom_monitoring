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
