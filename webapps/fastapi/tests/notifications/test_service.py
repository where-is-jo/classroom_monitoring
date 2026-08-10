"""알림 service의 격리·멱등·명시적 재시도 규칙 테스트."""

from __future__ import annotations

from typing import Literal

import pytest

from app.notifications.adapters.memory_repository import InMemoryNotificationRepository
from app.notifications.errors import (
    MockDeliveryNotRetryableError,
    NotificationDataInvalidError,
    NotificationNotFoundError,
)
from app.notifications.models import (
    CreateNotificationCommand,
    MockDeliveryStatus,
    NotificationDataValue,
    RetryMockDeliveryCommand,
)
from app.notifications.service import NotificationService
from app.users.models import User, UserRole
from tests.helpers.auth import AuthStack, build_auth_stack

type MockDeliveryMode = Literal["success", "fail_once", "always_fail"] | None
type NotificationServiceStack = tuple[
    AuthStack, User, InMemoryNotificationRepository, NotificationService
]


def _command(user_id: str, *, operation_id: str = "create-op") -> CreateNotificationCommand:
    return CreateNotificationCommand(
        recipient_user_id=user_id,
        type="INTERVIEW_READY",
        title="면담 준비 완료",
        body="담당 직원이 복귀했습니다.",
        data={"target_route": "/my/interview-waits", "interview_wait_id": "wait-1"},
        operation_id=operation_id,
        dedupe_key="interview-ready:wait-1",
    )


def _service(
    mode: MockDeliveryMode = "success", *, max_attempts: int = 3
) -> NotificationServiceStack:
    stack = build_auth_stack()
    recipient = stack.seed(UserRole.STUDENT)
    repository = InMemoryNotificationRepository()
    service = NotificationService(
        repository,
        stack.users,
        clock=stack.clock,
        mock_delivery_mode=mode,
        mock_delivery_max_attempts=max_attempts,
    )
    return stack, recipient, repository, service


def test_같은_dedupe_key와_operation_id는_알림과_delivery를_한번만_만든다() -> None:
    _, recipient, repository, service = _service()

    first = service.create(_command(recipient.id))
    retried_operation = service.create(_command(recipient.id))
    retried_dedupe = service.create(_command(recipient.id, operation_id="create-op-2"))

    assert first == retried_operation == retried_dedupe
    assert repository.count_unread(recipient.id) == 1
    deliveries = repository.list_notification_deliveries(first.id)
    assert len(deliveries) == 1
    assert deliveries[0].status == MockDeliveryStatus.SUCCESS


def test_알림은_사용자별로_격리되고_개별과_전체_읽음이_미읽음수에_반영된다() -> None:
    stack, recipient, _, service = _service(mode=None)
    other = stack.seed(UserRole.STAFF)
    first = service.create(_command(recipient.id))
    second = service.create(
        CreateNotificationCommand(
            recipient_user_id=recipient.id,
            type="GENERAL",
            title="두 번째",
            body="읽음 일괄 처리 테스트",
            data={},
            operation_id="second-op",
        )
    )

    assert service.unread_count(recipient) == 2
    assert (
        service.list_notifications(
            other, is_read=None, notification_type=None, limit=50, offset=0
        ).total
        == 0
    )
    with pytest.raises(NotificationNotFoundError):
        service.mark_read(other, first.id, operation_id="other-read")

    assert service.mark_read(recipient, first.id, operation_id="read-1").is_read
    assert service.unread_count(recipient) == 1
    assert service.mark_all_read(recipient, operation_id="read-all") == 1
    assert service.unread_count(recipient) == 0
    assert service.mark_all_read(recipient, operation_id="read-all") == 0
    assert service.mark_read(recipient, second.id, operation_id="read-2").is_read


def test_fail_once는_명시적_재시도로_성공하고_같은_operation은_중복되지_않는다() -> None:
    _, recipient, repository, service = _service(mode="fail_once")
    notification = service.create(_command(recipient.id))
    first = repository.list_notification_deliveries(notification.id)[0]

    retried = service.retry_mock_delivery(RetryMockDeliveryCommand(notification.id, "retry-op"))
    duplicated = service.retry_mock_delivery(RetryMockDeliveryCommand(notification.id, "retry-op"))

    assert first.status == MockDeliveryStatus.TEMPORARY_FAILURE
    assert retried == duplicated
    assert retried.attempt == 2
    assert retried.status == MockDeliveryStatus.SUCCESS
    assert len(repository.list_notification_deliveries(notification.id)) == 2


def test_always_fail은_최대시도에서_영구실패가_되고_기록을_보존한다() -> None:
    _, recipient, repository, service = _service(mode="always_fail", max_attempts=3)
    notification = service.create(_command(recipient.id))

    second = service.retry_mock_delivery(RetryMockDeliveryCommand(notification.id, "retry-2"))
    third = service.retry_mock_delivery(RetryMockDeliveryCommand(notification.id, "retry-3"))

    assert second.status == MockDeliveryStatus.TEMPORARY_FAILURE
    assert third.status == MockDeliveryStatus.PERMANENT_FAILURE
    with pytest.raises(MockDeliveryNotRetryableError):
        service.retry_mock_delivery(RetryMockDeliveryCommand(notification.id, "retry-4"))
    assert [item.attempt for item in repository.list_notification_deliveries(notification.id)] == [
        1,
        2,
        3,
    ]


@pytest.mark.parametrize(
    "data",
    [
        {"access_token": "secret"},
        {"target_route": "https://example.invalid"},
        {"target_route": "//example.invalid"},
        {"image_url": "/private/image"},
        {"confidence": float("nan")},
    ],
)
def test_민감하거나_외부인_data는_저장전에_거부된다(
    data: dict[str, NotificationDataValue],
) -> None:
    _, recipient, repository, service = _service(mode=None)
    command = _command(recipient.id)
    command = CreateNotificationCommand(
        recipient_user_id=command.recipient_user_id,
        type=command.type,
        title=command.title,
        body=command.body,
        data=data,
        operation_id=command.operation_id,
        dedupe_key=command.dedupe_key,
    )

    with pytest.raises(NotificationDataInvalidError):
        service.create(command)
    assert repository.count_unread(recipient.id) == 0
