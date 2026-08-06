"""알림 도메인 오류."""

from __future__ import annotations

from ..shared.errors import DomainError


class NotificationNotFoundError(DomainError):
    code = "NOTIFICATION_NOT_FOUND"
    status_code = 404

    def __init__(self) -> None:
        super().__init__("요청한 알림을 찾을 수 없습니다.")


class NotificationOperationConflictError(DomainError):
    code = "NOTIFICATION_OPERATION_CONFLICT"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("같은 작업 식별자가 다른 알림 요청에 사용됐습니다.")


class NotificationDedupeConflictError(DomainError):
    code = "NOTIFICATION_DEDUPE_CONFLICT"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("같은 중복 방지 키가 다른 알림 요청에 사용됐습니다.")


class NotificationDataInvalidError(DomainError):
    code = "NOTIFICATION_DATA_INVALID"
    status_code = 400

    def __init__(self, message: str = "알림 연결 데이터가 올바르지 않습니다.") -> None:
        super().__init__(message)


class NotificationRecipientUnavailableError(DomainError):
    code = "NOTIFICATION_RECIPIENT_UNAVAILABLE"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("활성 알림 수신자를 찾을 수 없습니다.")


class MockDeliveryNotRetryableError(DomainError):
    code = "MOCK_DELIVERY_NOT_RETRYABLE"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("이미 완료됐거나 최대 시도 횟수에 도달한 delivery입니다.")


class MockDeliveryDisabledError(DomainError):
    code = "MOCK_DELIVERY_DISABLED"
    status_code = 404

    def __init__(self) -> None:
        super().__init__("mock delivery 기능을 사용할 수 없습니다.")
