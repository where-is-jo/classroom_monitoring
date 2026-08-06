"""인앱 알림과 mock delivery PyMongo 어댑터."""

from __future__ import annotations

from datetime import datetime
from math import isfinite
from typing import Any

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from ...shared.database import MongoDatabase, MongoDocument
from ...shared.errors import RepositoryDataError, RepositoryUnavailableError
from ..errors import NotificationOperationConflictError
from ..models import (
    MockDelivery,
    MockDeliveryPage,
    MockDeliveryStatus,
    Notification,
    NotificationDataValue,
    NotificationPage,
)


class MongoNotificationRepository:
    notification_collection_name = "notifications"
    delivery_collection_name = "notification_deliveries"

    def __init__(self, database: MongoDatabase) -> None:
        self._notifications = database[self.notification_collection_name]
        self._deliveries = database[self.delivery_collection_name]

    @classmethod
    def ensure_indexes(cls, database: MongoDatabase) -> None:
        notifications = database[cls.notification_collection_name]
        notifications.create_index(
            [("created_operation_id", ASCENDING)],
            name="notifications_create_operation_unique",
            unique=True,
        )
        notifications.create_index(
            [("dedupe_key", ASCENDING)],
            name="notifications_dedupe_unique",
            unique=True,
            sparse=True,
        )
        notifications.create_index(
            [
                ("recipient_user_id", ASCENDING),
                ("is_read", ASCENDING),
                ("created_at", DESCENDING),
            ],
            name="notifications_recipient_read_created",
        )
        notifications.create_index(
            [
                ("recipient_user_id", ASCENDING),
                ("type", ASCENDING),
                ("created_at", DESCENDING),
            ],
            name="notifications_recipient_type_created",
        )
        deliveries = database[cls.delivery_collection_name]
        deliveries.create_index(
            [("notification_id", ASCENDING), ("attempt", ASCENDING)],
            name="notification_deliveries_notification_attempt_unique",
            unique=True,
        )
        deliveries.create_index(
            [("operation_id", ASCENDING)],
            name="notification_deliveries_operation_unique",
            unique=True,
        )
        deliveries.create_index(
            [("attempted_at", DESCENDING)],
            name="notification_deliveries_attempted_at",
        )

    def create_notification(self, notification: Notification) -> Notification:
        try:
            self._notifications.insert_one(self._notification_to_document(notification))
            return notification
        except DuplicateKeyError:
            existing = self.get_notification_by_operation_id(
                notification.created_operation_id
            )
            if existing is None and notification.dedupe_key is not None:
                existing = self.get_notification_by_dedupe_key(notification.dedupe_key)
            if existing is not None:
                return existing
            raise RepositoryUnavailableError() from None
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def get_notification(self, notification_id: str) -> Notification | None:
        return self._find_notification({"_id": notification_id})

    def get_notification_by_operation_id(
        self, operation_id: str
    ) -> Notification | None:
        return self._find_notification({"created_operation_id": operation_id})

    def get_notification_by_dedupe_key(
        self, dedupe_key: str
    ) -> Notification | None:
        return self._find_notification({"dedupe_key": dedupe_key})

    def _find_notification(self, query: MongoDocument) -> Notification | None:
        try:
            document = self._notifications.find_one(query)
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else self._notification_to_domain(document)

    def list_notifications(
        self,
        *,
        recipient_user_id: str,
        is_read: bool | None,
        notification_type: str | None,
        limit: int,
        offset: int,
    ) -> NotificationPage:
        query: MongoDocument = {"recipient_user_id": recipient_user_id}
        if is_read is not None:
            query["is_read"] = is_read
        if notification_type is not None:
            query["type"] = notification_type
        try:
            total = self._notifications.count_documents(query)
            documents = list(
                self._notifications.find(query)
                .sort([("created_at", DESCENDING), ("_id", DESCENDING)])
                .skip(offset)
                .limit(limit)
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return NotificationPage(
            items=[self._notification_to_domain(document) for document in documents],
            total=total,
        )

    def count_unread(self, recipient_user_id: str) -> int:
        try:
            return self._notifications.count_documents(
                {"recipient_user_id": recipient_user_id, "is_read": False}
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def mark_read(
        self,
        notification_id: str,
        *,
        recipient_user_id: str,
        read_at: datetime,
        operation_id: str,
    ) -> Notification | None:
        try:
            document = self._notifications.find_one_and_update(
                {
                    "_id": notification_id,
                    "recipient_user_id": recipient_user_id,
                    "is_read": False,
                },
                {
                    "$set": {
                        "is_read": True,
                        "read_at": read_at,
                        "read_operation_id": operation_id,
                    }
                },
                return_document=ReturnDocument.AFTER,
            )
            if document is None:
                document = self._notifications.find_one(
                    {"_id": notification_id, "recipient_user_id": recipient_user_id}
                )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else self._notification_to_domain(document)

    def mark_all_read(
        self,
        *,
        recipient_user_id: str,
        read_at: datetime,
        operation_id: str,
    ) -> int:
        try:
            result = self._notifications.update_many(
                {"recipient_user_id": recipient_user_id, "is_read": False},
                {
                    "$set": {
                        "is_read": True,
                        "read_at": read_at,
                        "read_operation_id": operation_id,
                    }
                },
            )
            return result.modified_count
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def append_delivery(self, delivery: MockDelivery) -> MockDelivery:
        try:
            self._deliveries.insert_one(self._delivery_to_document(delivery))
            return delivery
        except DuplicateKeyError:
            existing = self.get_delivery_by_operation_id(delivery.operation_id)
            if existing is not None:
                if existing.notification_id != delivery.notification_id:
                    raise NotificationOperationConflictError()
                return existing
            existing = self.get_delivery_by_attempt(
                delivery.notification_id, delivery.attempt
            )
            if existing is not None:
                return existing
            raise RepositoryUnavailableError() from None
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def get_delivery_by_operation_id(
        self, operation_id: str
    ) -> MockDelivery | None:
        return self._find_delivery({"operation_id": operation_id})

    def get_delivery_by_attempt(
        self, notification_id: str, attempt: int
    ) -> MockDelivery | None:
        return self._find_delivery(
            {"notification_id": notification_id, "attempt": attempt}
        )

    def _find_delivery(self, query: MongoDocument) -> MockDelivery | None:
        try:
            document = self._deliveries.find_one(query)
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else self._delivery_to_domain(document)

    def list_deliveries(
        self,
        *,
        status: str | None,
        limit: int,
        offset: int,
    ) -> MockDeliveryPage:
        query: MongoDocument = {}
        if status is not None:
            query["status"] = status
        try:
            total = self._deliveries.count_documents(query)
            documents = list(
                self._deliveries.find(query)
                .sort([("attempted_at", DESCENDING), ("_id", DESCENDING)])
                .skip(offset)
                .limit(limit)
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return MockDeliveryPage(
            items=[self._delivery_to_domain(document) for document in documents],
            total=total,
        )

    def list_notification_deliveries(
        self, notification_id: str
    ) -> list[MockDelivery]:
        try:
            documents = list(
                self._deliveries.find({"notification_id": notification_id}).sort(
                    [("attempt", ASCENDING)]
                )
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return [self._delivery_to_domain(document) for document in documents]

    @staticmethod
    def _notification_to_document(notification: Notification) -> MongoDocument:
        document: MongoDocument = {
            "_id": notification.id,
            "recipient_user_id": notification.recipient_user_id,
            "type": notification.type,
            "title": notification.title,
            "body": notification.body,
            "data": dict(notification.data),
            "is_read": notification.is_read,
            "read_at": notification.read_at,
            "created_at": notification.created_at,
            "created_operation_id": notification.created_operation_id,
            "read_operation_id": notification.read_operation_id,
        }
        if notification.dedupe_key is not None:
            document["dedupe_key"] = notification.dedupe_key
        return document

    @staticmethod
    def _notification_to_domain(document: MongoDocument) -> Notification:
        try:
            return Notification(
                id=_string(document, "_id"),
                recipient_user_id=_string(document, "recipient_user_id"),
                type=_string(document, "type"),
                title=_string(document, "title"),
                body=_string(document, "body"),
                data=_payload(document, "data"),
                is_read=_boolean(document, "is_read"),
                read_at=_optional_aware_datetime(document, "read_at"),
                dedupe_key=_optional_string(document, "dedupe_key"),
                created_at=_aware_datetime(document, "created_at"),
                created_operation_id=_string(document, "created_operation_id"),
                read_operation_id=_optional_string(document, "read_operation_id"),
            )
        except (KeyError, TypeError, ValueError):
            raise RepositoryDataError() from None

    @staticmethod
    def _delivery_to_document(delivery: MockDelivery) -> MongoDocument:
        return {
            "_id": delivery.id,
            "notification_id": delivery.notification_id,
            "provider": delivery.provider,
            "status": delivery.status.value,
            "attempt": delivery.attempt,
            "operation_id": delivery.operation_id,
            "request_payload": dict(delivery.request_payload),
            "result_payload": dict(delivery.result_payload),
            "error": delivery.error,
            "attempted_at": delivery.attempted_at,
        }

    @staticmethod
    def _delivery_to_domain(document: MongoDocument) -> MockDelivery:
        try:
            attempt = document["attempt"]
            if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
                raise TypeError("attempt must be a positive integer")
            provider = _string(document, "provider")
            if provider != "mock":
                raise ValueError("provider must be mock")
            return MockDelivery(
                id=_string(document, "_id"),
                notification_id=_string(document, "notification_id"),
                provider=provider,
                status=MockDeliveryStatus(_string(document, "status")),
                attempt=attempt,
                operation_id=_string(document, "operation_id"),
                request_payload=_payload(document, "request_payload"),
                result_payload=_payload(document, "result_payload"),
                error=_optional_string(document, "error"),
                attempted_at=_aware_datetime(document, "attempted_at"),
            )
        except (KeyError, TypeError, ValueError):
            raise RepositoryDataError() from None


def _string(document: MongoDocument, field: str) -> str:
    value = document[field]
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    return value


def _optional_string(document: MongoDocument, field: str) -> str | None:
    value = document.get(field)
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{field} must be a string or null")
    return value


def _boolean(document: MongoDocument, field: str) -> bool:
    value = document[field]
    if not isinstance(value, bool):
        raise TypeError(f"{field} must be a boolean")
    return value


def _aware_datetime(document: MongoDocument, field: str) -> datetime:
    value = document[field]
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise TypeError(f"{field} must be an aware datetime")
    return value


def _optional_aware_datetime(
    document: MongoDocument, field: str
) -> datetime | None:
    value = document.get(field)
    if value is not None and (
        not isinstance(value, datetime) or value.tzinfo is None
    ):
        raise TypeError(f"{field} must be an aware datetime or null")
    return value


def _payload(
    document: MongoDocument, field: str
) -> dict[str, NotificationDataValue]:
    value = document[field]
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise TypeError(f"{field} must be a string-keyed mapping")
    for item in value.values():
        if not isinstance(item, (str, int, float, bool, type(None))):
            raise TypeError(f"{field} contains an unsupported value")
        if isinstance(item, float) and not isfinite(item):
            raise TypeError(f"{field} contains a non-finite value")
    return dict(value)
