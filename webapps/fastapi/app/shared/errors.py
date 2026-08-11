"""도메인 예외와 오류 응답 형식.

오류 본문 형식은 docs/conventions/api-convention.md를 따른다.
내부 정보(스택 트레이스, 쿼리, 내부 경로)를 응답에 넣지 않는다.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DomainError(Exception):
    """서비스 계층이 던지는 예외의 기반 클래스.

    HTTP를 모른다. 상태 코드로의 변환은 라우터·예외 핸들러가 담당한다.
    """

    code = "INTERNAL_ERROR"
    status_code = 500

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class RepositoryUnavailableError(DomainError):
    code = "REPOSITORY_UNAVAILABLE"
    status_code = 503

    def __init__(self) -> None:
        super().__init__("데이터 저장소를 일시적으로 사용할 수 없습니다.")


class RepositoryDataError(DomainError):
    code = "REPOSITORY_DATA_INVALID"
    status_code = 500

    def __init__(self) -> None:
        super().__init__("저장된 데이터를 처리할 수 없습니다.")


class DatabaseUnavailableError(DomainError):
    code = "DATABASE_UNAVAILABLE"
    status_code = 503

    def __init__(self) -> None:
        super().__init__("데이터 저장소 준비 상태를 확인할 수 없습니다.")


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    """API 오류 응답 본문."""

    error: ErrorDetail
