"""인앱 알림 저장소 외부 I/O 포트."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .models import MockDelivery, MockDeliveryPage, Notification, NotificationPage


class NotificationRepository(Protocol):
    def create_notification(self, notification: Notification) -> Notification: ...

    def get_notification(self, notification_id: str) -> Notification | None: ...

    def get_notification_by_operation_id(self, operation_id: str) -> Notification | None: ...

    def get_notification_by_dedupe_key(self, dedupe_key: str) -> Notification | None: ...

    def list_notifications(
        self,
        *,
        recipient_user_id: str,
        is_read: bool | None,
        notification_type: str | None,
        limit: int,
        offset: int,
    ) -> NotificationPage: ...

    def count_unread(self, recipient_user_id: str) -> int: ...

    def mark_read(
        self,
        notification_id: str,
        *,
        recipient_user_id: str,
        read_at: datetime,
        operation_id: str,
    ) -> Notification | None: ...

    def mark_all_read(
        self,
        *,
        recipient_user_id: str,
        read_at: datetime,
        operation_id: str,
    ) -> int: ...

    def append_delivery(self, delivery: MockDelivery) -> MockDelivery: ...

    def get_delivery_by_operation_id(self, operation_id: str) -> MockDelivery | None: ...

    def get_delivery_by_attempt(
        self, notification_id: str, attempt: int
    ) -> MockDelivery | None: ...

    def list_deliveries(
        self,
        *,
        status: str | None,
        limit: int,
        offset: int,
    ) -> MockDeliveryPage: ...

    def list_notification_deliveries(self, notification_id: str) -> list[MockDelivery]: ...
