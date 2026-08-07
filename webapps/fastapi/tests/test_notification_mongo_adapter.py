"""알림 MongoDB index와 문서 변환 계약 테스트."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.notifications.adapters.mongo_repository import MongoNotificationRepository
from app.notifications.models import (
    MockDelivery,
    MockDeliveryStatus,
    Notification,
)
from app.shared.errors import RepositoryDataError


class RecordingCollection:
    def __init__(self) -> None:
        self.indexes: list[tuple[list[tuple[str, int]], dict[str, object]]] = []

    def create_index(self, fields: list[tuple[str, int]], **options: object) -> None:
        self.indexes.append((fields, options))


class RecordingDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, RecordingCollection] = {}

    def __getitem__(self, name: str) -> RecordingCollection:
        return self.collections.setdefault(name, RecordingCollection())


def _notification() -> Notification:
    now = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)
    return Notification(
        id="notification-id",
        recipient_user_id="user-id",
        type="INTERVIEW_READY",
        title="면담 준비 완료",
        body="담당 직원이 복귀했습니다.",
        data={"target_route": "/my/interview-waits", "wait_id": "wait-id"},
        is_read=False,
        read_at=None,
        dedupe_key="wait-ready:wait-id",
        created_at=now,
        created_operation_id="create-op",
    )


def test_알림_Mongo_index는_dedupe_수신자_읽음_attempt_operation을_보장한다() -> None:
    database = RecordingDatabase()

    MongoNotificationRepository.ensure_indexes(database)  # type: ignore[arg-type]

    notifications = database.collections["notifications"].indexes
    deliveries = database.collections["notification_deliveries"].indexes
    assert any(
        fields == [("dedupe_key", 1)] and options.get("unique") and options.get("sparse")
        for fields, options in notifications
    )
    assert any(
        fields[:2] == [("recipient_user_id", 1), ("is_read", 1)] for fields, _ in notifications
    )
    assert any(
        fields == [("notification_id", 1), ("attempt", 1)] and options.get("unique")
        for fields, options in deliveries
    )
    assert any(
        fields == [("operation_id", 1)] and options.get("unique") for fields, options in deliveries
    )


def test_알림과_delivery_document_roundtrip은_UTC와_정제_payload를_보존한다() -> None:
    notification = _notification()
    delivery = MockDelivery(
        id="delivery-id",
        notification_id=notification.id,
        provider="mock",
        status=MockDeliveryStatus.TEMPORARY_FAILURE,
        attempt=1,
        operation_id="delivery-op",
        request_payload={"notification_id": notification.id, "type": notification.type},
        result_payload={"outcome": "rejected"},
        error="mock delivery를 일시적으로 처리하지 못했습니다.",
        attempted_at=notification.created_at,
    )

    notification_document = MongoNotificationRepository._notification_to_document(notification)
    delivery_document = MongoNotificationRepository._delivery_to_document(delivery)

    assert (
        MongoNotificationRepository._notification_to_domain(notification_document) == notification
    )
    assert MongoNotificationRepository._delivery_to_domain(delivery_document) == delivery
    assert not (
        {"password", "token", "cookie", "image", "video"}
        & delivery_document["request_payload"].keys()
    )


def test_잘못된_payload와_naive_datetime은_내부값_없는_저장소오류가_된다() -> None:
    document = MongoNotificationRepository._notification_to_document(_notification())
    document["data"] = {"nested": {"unsupported": True}}

    with pytest.raises(RepositoryDataError):
        MongoNotificationRepository._notification_to_domain(document)

    document = MongoNotificationRepository._notification_to_document(_notification())
    document["created_at"] = datetime(2026, 8, 5, 9, 0)  # noqa: DTZ001
    with pytest.raises(RepositoryDataError):
        MongoNotificationRepository._notification_to_domain(document)
