"""감사 로그 operation 충돌 오류."""

from __future__ import annotations

from ..shared.errors import DomainError


class AuditOperationConflictError(DomainError):
    code = "AUDIT_OPERATION_CONFLICT"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("같은 operation_id가 다른 변경에 사용되었습니다.")
