"""외부 의존 없는 알림 저장소."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from threading import RLock

from ..errors import NotificationOperationConflictError
from ..models import MockDelivery, MockDeliveryPage, Notification, NotificationPage


class InMemoryNotificationRepository:
    def __init__(self) -> None:
        self._notifications: dict[str, Notification] = {}
        self._deliveries: dict[str, MockDelivery] = {}
        self._lock = RLock()

    def create_notification(self, notification: Notification) -> Notification:
        with self._lock:
            operation_owner = self.get_notification_by_operation_id(
                notification.created_operation_id
            )
            if operation_owner is not None:
                return operation_owner
            if notification.dedupe_key is not None:
                dedupe_owner = self.get_notification_by_dedupe_key(notification.dedupe_key)
                if dedupe_owner is not None:
                    return dedupe_owner
            self._notifications[notification.id] = notification
            return notification

    def get_notification(self, notification_id: str) -> Notification | None:
        with self._lock:
            return self._notifications.get(notification_id)

    def dashboard_snapshot(
        self,
    ) -> tuple[list[Notification], list[MockDelivery]]:
        """Return an immutable-value snapshot for the local admin read model."""
        with self._lock:
            return list(self._notifications.values()), list(self._deliveries.values())

    def get_notification_by_operation_id(self, operation_id: str) -> Notification | None:
        with self._lock:
            return next(
                (
                    notification
                    for notification in self._notifications.values()
                    if notification.created_operation_id == operation_id
                ),
                None,
            )

    def get_notification_by_dedupe_key(self, dedupe_key: str) -> Notification | None:
        with self._lock:
            return next(
                (
                    notification
                    for notification in self._notifications.values()
                    if notification.dedupe_key == dedupe_key
                ),
                None,
            )

    def list_notifications(
        self,
        *,
        recipient_user_id: str,
        is_read: bool | None,
        notification_type: str | None,
        limit: int,
        offset: int,
    ) -> NotificationPage:
        with self._lock:
            notifications = [
                notification
                for notification in self._notifications.values()
                if notification.recipient_user_id == recipient_user_id
            ]
        if is_read is not None:
            notifications = [
                notification for notification in notifications if notification.is_read is is_read
            ]
        if notification_type is not None:
            notifications = [
                notification
                for notification in notifications
                if notification.type == notification_type
            ]
        notifications.sort(
            key=lambda notification: (notification.created_at, notification.id),
            reverse=True,
        )
        return NotificationPage(
            items=notifications[offset : offset + limit],
            total=len(notifications),
        )

    def count_unread(self, recipient_user_id: str) -> int:
        with self._lock:
            return sum(
                1
                for notification in self._notifications.values()
                if notification.recipient_user_id == recipient_user_id and not notification.is_read
            )

    def mark_read(
        self,
        notification_id: str,
        *,
        recipient_user_id: str,
        read_at: datetime,
        operation_id: str,
    ) -> Notification | None:
        with self._lock:
            notification = self._notifications.get(notification_id)
            if notification is None or notification.recipient_user_id != recipient_user_id:
                return None
            if notification.is_read:
                return notification
            updated = replace(
                notification,
                is_read=True,
                read_at=read_at,
                read_operation_id=operation_id,
            )
            self._notifications[notification_id] = updated
            return updated

    def mark_all_read(
        self,
        *,
        recipient_user_id: str,
        read_at: datetime,
        operation_id: str,
    ) -> int:
        with self._lock:
            unread_ids = [
                notification.id
                for notification in self._notifications.values()
                if notification.recipient_user_id == recipient_user_id and not notification.is_read
            ]
            for notification_id in unread_ids:
                self._notifications[notification_id] = replace(
                    self._notifications[notification_id],
                    is_read=True,
                    read_at=read_at,
                    read_operation_id=operation_id,
                )
            return len(unread_ids)

    def append_delivery(self, delivery: MockDelivery) -> MockDelivery:
        with self._lock:
            operation_owner = self.get_delivery_by_operation_id(delivery.operation_id)
            if operation_owner is not None:
                if operation_owner.notification_id != delivery.notification_id:
                    raise NotificationOperationConflictError()
                return operation_owner
            attempt_owner = self.get_delivery_by_attempt(delivery.notification_id, delivery.attempt)
            if attempt_owner is not None:
                return attempt_owner
            self._deliveries[delivery.id] = delivery
            return delivery

    def get_delivery_by_operation_id(self, operation_id: str) -> MockDelivery | None:
        with self._lock:
            return next(
                (
                    delivery
                    for delivery in self._deliveries.values()
                    if delivery.operation_id == operation_id
                ),
                None,
            )

    def get_delivery_by_attempt(self, notification_id: str, attempt: int) -> MockDelivery | None:
        with self._lock:
            return next(
                (
                    delivery
                    for delivery in self._deliveries.values()
                    if delivery.notification_id == notification_id and delivery.attempt == attempt
                ),
                None,
            )

    def list_deliveries(
        self,
        *,
        status: str | None,
        limit: int,
        offset: int,
    ) -> MockDeliveryPage:
        with self._lock:
            deliveries = list(self._deliveries.values())
        if status is not None:
            deliveries = [delivery for delivery in deliveries if delivery.status.value == status]
        deliveries.sort(
            key=lambda delivery: (delivery.attempted_at, delivery.id),
            reverse=True,
        )
        return MockDeliveryPage(
            items=deliveries[offset : offset + limit],
            total=len(deliveries),
        )

    def list_notification_deliveries(self, notification_id: str) -> list[MockDelivery]:
        with self._lock:
            deliveries = [
                delivery
                for delivery in self._deliveries.values()
                if delivery.notification_id == notification_id
            ]
        return sorted(deliveries, key=lambda delivery: delivery.attempt)
