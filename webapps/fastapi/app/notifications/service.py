"""FastAPI와 저장 기술에 의존하지 않는 알림 규칙."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import datetime
from math import isfinite
from typing import Literal
from uuid import uuid4

from ..users.models import User, UserStatus
from ..users.ports import UserRepository
from .errors import (
    MockDeliveryDisabledError,
    MockDeliveryNotRetryableError,
    NotificationDataInvalidError,
    NotificationDedupeConflictError,
    NotificationNotFoundError,
    NotificationOperationConflictError,
    NotificationRecipientUnavailableError,
)
from .models import (
    CreateNotificationCommand,
    MockDelivery,
    MockDeliveryPage,
    MockDeliveryStatus,
    Notification,
    NotificationDataValue,
    NotificationPage,
    RetryMockDeliveryCommand,
)
from .ports import NotificationRepository

MockDeliveryMode = Literal["success", "fail_once", "always_fail"]

_TYPE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_SENSITIVE_KEY_PARTS = (
    "password",
    "token",
    "cookie",
    "secret",
    "authorization",
    "image",
    "video",
    "snapshot",
    "biometric",
)
_ALLOWED_TARGET_PREFIXES = (
    "/employees",
    "/my/interview-waits",
    "/staff/interview-waits",
    "/classrooms",
    "/notifications",
    "/admin/alerts",
)


class NotificationService:
    def __init__(
        self,
        repository: NotificationRepository,
        user_repository: UserRepository,
        *,
        clock: Callable[[], datetime],
        mock_delivery_mode: MockDeliveryMode | None,
        mock_delivery_max_attempts: int = 3,
    ) -> None:
        self._repository = repository
        self._user_repository = user_repository
        self._clock = clock
        self._mock_delivery_mode = mock_delivery_mode
        self._mock_delivery_max_attempts = mock_delivery_max_attempts

    def create(self, command: CreateNotificationCommand) -> Notification:
        """내부 기능이 호출하는 멱등 인앱 알림 생성 API."""
        normalized = self._normalize_command(command)
        self._require_active_recipient(normalized.recipient_user_id)

        operation_owner = self._repository.get_notification_by_operation_id(normalized.operation_id)
        if operation_owner is not None:
            self._assert_same_create(operation_owner, normalized, check_dedupe=True)
            self._ensure_initial_mock_delivery(operation_owner)
            return operation_owner

        if normalized.dedupe_key is not None:
            dedupe_owner = self._repository.get_notification_by_dedupe_key(normalized.dedupe_key)
            if dedupe_owner is not None:
                self._assert_same_create(dedupe_owner, normalized, check_dedupe=False)
                self._ensure_initial_mock_delivery(dedupe_owner)
                return dedupe_owner

        notification = Notification(
            id=str(uuid4()),
            recipient_user_id=normalized.recipient_user_id,
            type=normalized.type,
            title=normalized.title,
            body=normalized.body,
            data=dict(normalized.data),
            is_read=False,
            read_at=None,
            dedupe_key=normalized.dedupe_key,
            created_at=self._clock(),
            created_operation_id=normalized.operation_id,
        )
        stored = self._repository.create_notification(notification)
        if stored.id != notification.id:
            self._assert_same_create(
                stored,
                normalized,
                check_dedupe=stored.created_operation_id == normalized.operation_id,
            )
        self._ensure_initial_mock_delivery(stored)
        return stored

    def list_notifications(
        self,
        actor: User,
        *,
        is_read: bool | None,
        notification_type: str | None,
        limit: int,
        offset: int,
    ) -> NotificationPage:
        normalized_type = (
            self._normalize_type(notification_type) if notification_type is not None else None
        )
        return self._repository.list_notifications(
            recipient_user_id=actor.id,
            is_read=is_read,
            notification_type=normalized_type,
            limit=limit,
            offset=offset,
        )

    def unread_count(self, actor: User) -> int:
        return self._repository.count_unread(actor.id)

    def mark_read(self, actor: User, notification_id: str, *, operation_id: str) -> Notification:
        notification = self._repository.mark_read(
            notification_id,
            recipient_user_id=actor.id,
            read_at=self._clock(),
            operation_id=operation_id,
        )
        if notification is None:
            raise NotificationNotFoundError()
        return notification

    def mark_all_read(self, actor: User, *, operation_id: str) -> int:
        return self._repository.mark_all_read(
            recipient_user_id=actor.id,
            read_at=self._clock(),
            operation_id=operation_id,
        )

    def list_mock_deliveries(
        self,
        *,
        status: MockDeliveryStatus | None,
        limit: int,
        offset: int,
    ) -> MockDeliveryPage:
        self._require_mock_delivery_enabled()
        return self._repository.list_deliveries(
            status=None if status is None else status.value,
            limit=limit,
            offset=offset,
        )

    def retry_mock_delivery(self, command: RetryMockDeliveryCommand) -> MockDelivery:
        self._require_mock_delivery_enabled()
        operation_owner = self._repository.get_delivery_by_operation_id(command.operation_id)
        if operation_owner is not None:
            if operation_owner.notification_id != command.notification_id:
                raise NotificationOperationConflictError()
            return operation_owner

        notification = self._repository.get_notification(command.notification_id)
        if notification is None:
            raise NotificationNotFoundError()
        deliveries = self._repository.list_notification_deliveries(notification.id)
        if not deliveries:
            return self._record_mock_delivery(
                notification, attempt=1, operation_id=command.operation_id
            )
        latest = deliveries[-1]
        if (
            latest.status in {MockDeliveryStatus.SUCCESS, MockDeliveryStatus.PERMANENT_FAILURE}
            or latest.attempt >= self._mock_delivery_max_attempts
        ):
            raise MockDeliveryNotRetryableError()
        return self._record_mock_delivery(
            notification,
            attempt=latest.attempt + 1,
            operation_id=command.operation_id,
        )

    def target_route(self, notification: Notification) -> str | None:
        route = notification.data.get("target_route")
        if not isinstance(route, str):
            return None
        return route if self._is_allowed_target_route(route) else None

    def _normalize_command(self, command: CreateNotificationCommand) -> CreateNotificationCommand:
        title = command.title.strip()
        body = command.body.strip()
        if not title or len(title) > 200:
            raise NotificationDataInvalidError("알림 제목은 1자 이상 200자 이하여야 합니다.")
        if not body or len(body) > 1000:
            raise NotificationDataInvalidError("알림 본문은 1자 이상 1000자 이하여야 합니다.")
        operation_id = command.operation_id.strip()
        if not operation_id or len(operation_id) > 128:
            raise NotificationDataInvalidError("작업 식별자가 올바르지 않습니다.")
        dedupe_key = command.dedupe_key.strip() if command.dedupe_key else None
        if dedupe_key is not None and len(dedupe_key) > 200:
            raise NotificationDataInvalidError("중복 방지 키가 너무 깁니다.")
        return CreateNotificationCommand(
            recipient_user_id=command.recipient_user_id.strip(),
            type=self._normalize_type(command.type),
            title=title,
            body=body,
            data=self._sanitize_data(command.data),
            operation_id=operation_id,
            dedupe_key=dedupe_key,
        )

    def _require_active_recipient(self, recipient_user_id: str) -> None:
        recipient = self._user_repository.get_user(recipient_user_id)
        if recipient is None or recipient.status != UserStatus.ACTIVE:
            raise NotificationRecipientUnavailableError()

    @staticmethod
    def _normalize_type(notification_type: str) -> str:
        normalized = notification_type.strip().upper()
        if not _TYPE_PATTERN.fullmatch(normalized):
            raise NotificationDataInvalidError("알림 type이 올바르지 않습니다.")
        return normalized

    def _sanitize_data(
        self, data: Mapping[str, NotificationDataValue]
    ) -> dict[str, NotificationDataValue]:
        if len(data) > 20:
            raise NotificationDataInvalidError("알림 연결 데이터 항목이 너무 많습니다.")
        sanitized: dict[str, NotificationDataValue] = {}
        for key, value in data.items():
            normalized_key = key.strip()
            lowered_key = normalized_key.lower()
            if (
                not normalized_key
                or len(normalized_key) > 64
                or any(part in lowered_key for part in _SENSITIVE_KEY_PARTS)
            ):
                raise NotificationDataInvalidError()
            if not isinstance(value, (str, int, float, bool, type(None))):
                raise NotificationDataInvalidError()
            if isinstance(value, float) and not isfinite(value):
                raise NotificationDataInvalidError()
            if isinstance(value, str) and len(value) > 500:
                raise NotificationDataInvalidError()
            if normalized_key == "target_route" and (
                not isinstance(value, str) or not self._is_allowed_target_route(value)
            ):
                raise NotificationDataInvalidError("허용되지 않은 내부 연결 경로입니다.")
            sanitized[normalized_key] = value
        return sanitized

    @staticmethod
    def _is_allowed_target_route(route: str) -> bool:
        if (
            not route.startswith("/")
            or "?" in route
            or "#" in route
            or "\\" in route
            or "//" in route
        ):
            return False
        return any(
            route == prefix or route.startswith(prefix + "/") for prefix in _ALLOWED_TARGET_PREFIXES
        )

    @staticmethod
    def _assert_same_create(
        notification: Notification,
        command: CreateNotificationCommand,
        *,
        check_dedupe: bool,
    ) -> None:
        same_payload = (
            notification.recipient_user_id == command.recipient_user_id
            and notification.type == command.type
            and notification.title == command.title
            and notification.body == command.body
            and notification.data == command.data
            and notification.dedupe_key == command.dedupe_key
        )
        if same_payload:
            return
        if check_dedupe:
            raise NotificationOperationConflictError()
        raise NotificationDedupeConflictError()

    def _ensure_initial_mock_delivery(self, notification: Notification) -> None:
        if self._mock_delivery_mode is None:
            return
        if self._repository.get_delivery_by_attempt(notification.id, 1) is not None:
            return
        self._record_mock_delivery(
            notification,
            attempt=1,
            operation_id=f"notification:{notification.id}:mock:1",
        )

    def _record_mock_delivery(
        self,
        notification: Notification,
        *,
        attempt: int,
        operation_id: str,
    ) -> MockDelivery:
        assert self._mock_delivery_mode is not None
        if self._mock_delivery_mode == "success":
            delivery_status = MockDeliveryStatus.SUCCESS
            result_payload: dict[str, NotificationDataValue] = {"outcome": "accepted"}
            error = None
        elif self._mock_delivery_mode == "fail_once" and attempt > 1:
            delivery_status = MockDeliveryStatus.SUCCESS
            result_payload = {"outcome": "accepted_after_retry"}
            error = None
        else:
            is_final = attempt >= self._mock_delivery_max_attempts
            delivery_status = (
                MockDeliveryStatus.PERMANENT_FAILURE
                if is_final
                else MockDeliveryStatus.TEMPORARY_FAILURE
            )
            result_payload = {"outcome": "rejected"}
            error = (
                "mock delivery 최대 시도 횟수에 도달했습니다."
                if is_final
                else "mock delivery를 일시적으로 처리하지 못했습니다."
            )
        delivery = MockDelivery(
            id=str(uuid4()),
            notification_id=notification.id,
            provider="mock",
            status=delivery_status,
            attempt=attempt,
            operation_id=operation_id,
            request_payload={
                "notification_id": notification.id,
                "recipient_user_id": notification.recipient_user_id,
                "type": notification.type,
            },
            result_payload=result_payload,
            error=error,
            attempted_at=self._clock(),
        )
        return self._repository.append_delivery(delivery)

    def _require_mock_delivery_enabled(self) -> None:
        if self._mock_delivery_mode is None:
            raise MockDeliveryDisabledError()
