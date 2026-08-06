"""Classroom domain errors."""

from __future__ import annotations

from ..shared.errors import DomainError


class ClassroomNotFoundError(DomainError):
    code = "CLASSROOM_NOT_FOUND"
    status_code = 404

    def __init__(self) -> None:
        super().__init__("요청한 강의실을 찾을 수 없습니다.")


class SeatNotFoundError(DomainError):
    code = "SEAT_NOT_FOUND"
    status_code = 404

    def __init__(self) -> None:
        super().__init__("요청한 좌석을 찾을 수 없습니다.")


class AfterHoursAlertNotFoundError(DomainError):
    code = "AFTER_HOURS_ALERT_NOT_FOUND"
    status_code = 404


    def __init__(self) -> None:
        super().__init__("요청한 마감 후 경고를 찾을 수 없습니다.")


class ClassroomDuplicateError(DomainError):
    code = "CLASSROOM_DUPLICATE"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("같은 코드를 사용하는 강의실이 이미 있습니다.")


class SeatDuplicateError(DomainError):
    code = "SEAT_DUPLICATE"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("이 강의실에 같은 코드를 사용하는 좌석이 이미 있습니다.")


class ClassroomInputError(DomainError):
    code = "CLASSROOM_INPUT_INVALID"
    status_code = 422

    def __init__(self, message: str = "강의실 또는 좌석 입력 값이 올바르지 않습니다.") -> None:
        super().__init__(message)


class SeatBatchConflictError(DomainError):
    code = "SEAT_OBSERVATION_BATCH_CONFLICT"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("같은 event_id가 다른 좌석 관측 batch에 사용됐습니다.")


class ClassroomOperationConflictError(DomainError):
    code = "CLASSROOM_OPERATION_CONFLICT"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("같은 작업 식별자가 다른 요청에 사용됐습니다.")


class ClassroomConcurrentUpdateError(DomainError):
    code = "CLASSROOM_CONCURRENT_UPDATE"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("리소스가 동시에 변경됐습니다. 새로고침 후 다시 시도해 주세요.")


class AfterHoursAlertTransitionError(DomainError):
    code = "AFTER_HOURS_ALERT_INVALID_TRANSITION"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("현재 경고 상태에서는 요청한 변경을 수행할 수 없습니다.")
