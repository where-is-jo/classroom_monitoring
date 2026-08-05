"""감사 로그 도메인 모델."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class AuditLog:
    id: str
    operation_id: str
    actor_user_id: str | None
    action: str
    resource_type: str
    resource_id: str
    before: dict[str, Any]
    after: dict[str, Any]
    ip_fingerprint: str | None
    occurred_at: datetime
