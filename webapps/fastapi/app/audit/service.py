"""민감정보를 제거한 감사 로그 기록 service."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any
from uuid import uuid4

from .errors import AuditOperationConflictError
from .models import AuditLog
from .ports import AuditRepository

_SENSITIVE_KEY_PARTS = (
    "password",
    "hash",
    "token",
    "cookie",
    "authorization",
    "secret",
)


class AuditService:
    def __init__(
        self,
        repository: AuditRepository,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._clock = clock

    def get_by_operation_id(self, operation_id: str) -> AuditLog | None:
        return self._repository.get_by_operation_id(operation_id)

    def record(
        self,
        *,
        operation_id: str,
        actor_user_id: str | None,
        action: str,
        resource_type: str,
        resource_id: str,
        before: Mapping[str, Any] | None,
        after: Mapping[str, Any] | None,
        ip_fingerprint: str | None,
    ) -> AuditLog:
        existing = self._repository.get_by_operation_id(operation_id)
        if existing is not None:
            if (
                existing.action != action
                or existing.resource_type != resource_type
                or existing.resource_id != resource_id
            ):
                raise AuditOperationConflictError()
            return existing

        audit_log = AuditLog(
            id=str(uuid4()),
            operation_id=operation_id,
            actor_user_id=actor_user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            before=_sanitize_mapping(before or {}),
            after=_sanitize_mapping(after or {}),
            ip_fingerprint=ip_fingerprint,
            occurred_at=self._clock(),
        )
        return self._repository.append(audit_log)


def _sanitize_mapping(values: Mapping[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in values.items():
        normalized_key = key.lower()
        if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
            continue
        sanitized[key] = _sanitize_value(value)
    return sanitized


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _sanitize_mapping(value)
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return "[unsupported]"
