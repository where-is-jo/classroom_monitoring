"""인앱 알림과 mock delivery 도메인 값."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

NotificationDataValue = str | int | float | bool | None


class MockDeliveryStatus(StrEnum):
    SUCCESS = "SUCCESS"
    TEMPORARY_FAILURE = "TEMPORARY_FAILURE"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"


@dataclass(frozen=True)
class Notification:
    id: str
    recipient_user_id: str
    type: str
    title: str
    body: str
    data: dict[str, NotificationDataValue]
    is_read: bool
    read_at: datetime | None
    dedupe_key: str | None
    created_at: datetime
    created_operation_id: str
    read_operation_id: str | None = None


@dataclass(frozen=True)
class NotificationPage:
    items: list[Notification]
    total: int


@dataclass(frozen=True)
class MockDelivery:
    id: str
    notification_id: str
    provider: str
    status: MockDeliveryStatus
    attempt: int
    operation_id: str
    request_payload: dict[str, NotificationDataValue]
    result_payload: dict[str, NotificationDataValue]
    error: str | None
    attempted_at: datetime


@dataclass(frozen=True)
class MockDeliveryPage:
    items: list[MockDelivery]
    total: int


@dataclass(frozen=True)
class CreateNotificationCommand:
    recipient_user_id: str
    type: str
    title: str
    body: str
    data: dict[str, NotificationDataValue]
    operation_id: str
    dedupe_key: str | None = None


@dataclass(frozen=True)
class RetryMockDeliveryCommand:
    notification_id: str
    operation_id: str
