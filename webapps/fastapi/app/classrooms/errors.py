"""강의실 좌석 도메인 오류."""

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


class ClassroomDuplicateError(DomainError):
    code = "CLASSROOM_DUPLICATE"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("강의실 식별자 또는 코드가 중복됩니다.")


class SeatDuplicateError(DomainError):
    code = "SEAT_DUPLICATE"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("좌석 식별자 또는 코드가 중복됩니다.")


class SeatBatchConflictError(DomainError):
    code = "SEAT_BATCH_CONFLICT"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("같은 event ID에 다른 관측이 있습니다.")


class ClassroomConcurrentUpdateError(DomainError):
    code = "CLASSROOM_CONCURRENT_UPDATE"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("좌석 상태가 동시에 변경됐습니다.")


class ClassroomInputError(DomainError):
    code = "CLASSROOM_INPUT_INVALID"
    status_code = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
