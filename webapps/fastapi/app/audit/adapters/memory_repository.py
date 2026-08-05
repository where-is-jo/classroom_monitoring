"""외부 의존 없는 감사 로그 저장소."""

from __future__ import annotations

from threading import RLock

from ..errors import AuditOperationConflictError
from ..models import AuditLog


class InMemoryAuditRepository:
    def __init__(self) -> None:
        self._logs_by_operation_id: dict[str, AuditLog] = {}
        self._lock = RLock()

    def append(self, audit_log: AuditLog) -> AuditLog:
        with self._lock:
            existing = self._logs_by_operation_id.get(audit_log.operation_id)
            if existing is not None:
                if (
                    existing.action != audit_log.action
                    or existing.resource_type != audit_log.resource_type
                    or existing.resource_id != audit_log.resource_id
                ):
                    raise AuditOperationConflictError()
                return existing
            self._logs_by_operation_id[audit_log.operation_id] = audit_log
            return audit_log

    def get_by_operation_id(self, operation_id: str) -> AuditLog | None:
        with self._lock:
            return self._logs_by_operation_id.get(operation_id)

    def list_all(self) -> list[AuditLog]:
        with self._lock:
            return list(self._logs_by_operation_id.values())
