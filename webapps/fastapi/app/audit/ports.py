"""감사 로그 저장소 외부 I/O 포트."""

from __future__ import annotations

from typing import Protocol

from .models import AuditLog


class AuditRepository(Protocol):
    def append(self, audit_log: AuditLog) -> AuditLog: ...

    def get_by_operation_id(self, operation_id: str) -> AuditLog | None: ...
