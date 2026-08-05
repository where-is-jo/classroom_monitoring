"""도메인 예외와 오류 응답 형식.

오류 본문 형식은 docs/conventions/api-convention.md를 따른다.
내부 정보(스택 트레이스, 쿼리, 내부 경로)를 응답에 넣지 않는다.
"""

from __future__ import annotations

from pydantic import BaseModel


class DomainError(Exception):
    """서비스 계층이 던지는 예외의 기반 클래스.

    HTTP를 모른다. 상태 코드로의 변환은 라우터·예외 핸들러가 담당한다.
    """

    code = "INTERNAL_ERROR"
    status_code = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class EventNotFoundError(DomainError):
    code = "EVENT_NOT_FOUND"
    status_code = 404

    def __init__(self, event_id: str) -> None:
        super().__init__("요청한 이벤트를 찾을 수 없습니다.")
        self.event_id = event_id


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict = {}


class ErrorResponse(BaseModel):
    """API 오류 응답 본문."""

    error: ErrorDetail
