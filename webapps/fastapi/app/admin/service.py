"""Read-only administrator dashboard application service."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from ..auth.errors import PermissionDeniedError
from ..users.models import ADMIN_ROLES, User, UserStatus
from .errors import AdminQueryInputError
from .models import (
    AuditLogPage,
    DashboardActivityPage,
    DashboardActivityType,
    DashboardSummary,
)
from .ports import AdminDashboardRepository

_SENSITIVE_KEY_PARTS = (
    "password",
    "hash",
    "token",
    "cookie",
    "authorization",
    "secret",
)


class AdminDashboardService:
    def __init__(
        self, repository: AdminDashboardRepository, *, clock: Callable[[], datetime]
    ) -> None:
        self._repository = repository
        self._clock = clock

    def get_summary(
        self,
        actor: User,
        *,
        department: str | None = None,
        classroom_id: str | None = None,
    ) -> DashboardSummary:
        self._require_admin(actor)
        now = self._clock()
        snapshot = self._repository.get_snapshot(
            department=_optional_text(department),
            classroom_id=_optional_text(classroom_id),
            delivery_failure_since=now - timedelta(hours=24),
        )
        return DashboardSummary(
            generated_at=now,
            employees=snapshot.employees,
            interview_waits=snapshot.interview_waits,
            classrooms=snapshot.classrooms,
            alerts=snapshot.alerts,
            notifications=snapshot.notifications,
        )

    def list_activities(
        self,
        actor: User,
        *,
        activity_type: DashboardActivityType | None = None,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> DashboardActivityPage:
        self._require_admin(actor)
        now = self._clock()
        resolved_from = from_time or now - timedelta(hours=24)
        # The implicit window includes events produced at the current clock tick.
        # Explicit ``to`` remains an exclusive upper bound for stable paging.
        resolved_to = to_time or now + timedelta(microseconds=1)
        _validate_time_range(resolved_from, resolved_to)
        return self._repository.list_activities(
            activity_type=activity_type,
            from_time=resolved_from,
            to_time=resolved_to,
            limit=limit,
            offset=offset,
        )

    def list_audit_logs(
        self,
        actor: User,
        *,
        actor_user_id: str | None = None,
        action: str | None = None,
        resource: str | None = None,
        from_time: datetime | None = None,
        to_time: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> AuditLogPage:
        self._require_admin(actor)
        if from_time is not None and to_time is not None:
            _validate_time_range(from_time, to_time)
        page = self._repository.list_audit_logs(
            actor_user_id=_optional_text(actor_user_id),
            action=_optional_text(action),
            resource=_optional_text(resource),
            from_time=from_time,
            to_time=to_time,
            limit=limit,
            offset=offset,
        )
        return AuditLogPage(
            items=[
                replace(
                    item,
                    before=_mask_mapping(item.before),
                    after=_mask_mapping(item.after),
                )
                for item in page.items
            ],
            total=page.total,
        )

    @staticmethod
    def _require_admin(actor: User) -> None:
        if actor.status != UserStatus.ACTIVE or actor.role not in ADMIN_ROLES:
            raise PermissionDeniedError()


def _optional_text(value: str | None) -> str | None:
    normalized = value.strip() if value is not None else ""
    return normalized or None


def _validate_time_range(from_time: datetime, to_time: datetime) -> None:
    if from_time.tzinfo is None or to_time.tzinfo is None:
        raise AdminQueryInputError("조회 시각에는 시간대가 필요합니다.")
    if from_time >= to_time:
        raise AdminQueryInputError("from은 to보다 이전이어야 합니다.")


def _mask_mapping(values: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values.items():
        if any(part in key.lower() for part in _SENSITIVE_KEY_PARTS):
            result[key] = "[masked]"
        elif isinstance(value, Mapping):
            result[key] = _mask_mapping(value)
        elif isinstance(value, (list, tuple)):
            result[key] = [_mask_value(item) for item in value]
        else:
            result[key] = _mask_value(value)
    return result


def _mask_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _mask_mapping(value)
    if isinstance(value, (list, tuple)):
        return [_mask_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return "[unsupported]"
