"""Administrator dashboard query errors."""

from __future__ import annotations

from ..shared.errors import DomainError


class AdminQueryInputError(DomainError):
    code = "ADMIN_QUERY_INPUT_INVALID"
    status_code = 422

    def __init__(self, message: str = "조회 조건이 올바르지 않습니다.") -> None:
        super().__init__(message)
