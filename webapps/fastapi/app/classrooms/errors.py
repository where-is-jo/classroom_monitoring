"""강의실 좌석 도메인 오류.

학생 관련 neutral 오류(CLASSROOM_INPUT_INVALID·STUDENT_NOT_FOUND·
STUDENT_INACTIVE_FOR_ASSIGNMENT)는 `app/shared/errors.py`로 rehome되어
여기서는 re-export만 한다.
"""

from ..shared.errors import (
    ClassroomInputError,
    DomainError,
    StudentInactiveForAssignmentError,
)

__all__ = [
    "ClassroomConcurrentUpdateError",
    "ClassroomDuplicateError",
    "ClassroomInputError",
    "ClassroomNotFoundError",
    "SeatBatchConflictError",
    "SeatDuplicateError",
    "SeatInactiveForAssignmentError",
    "SeatNotFoundError",
    "StudentInactiveForAssignmentError",
]


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


# ============================================================
# 좌석-학생 지정 오류
# ============================================================


class SeatInactiveForAssignmentError(DomainError):
    """비활성화된 좌석에 학생 지정 시도."""

    code = "SEAT_INACTIVE_FOR_ASSIGNMENT"
    status_code = 422

    def __init__(self) -> None:
        super().__init__("비활성화된 좌석에는 학생을 지정할 수 없습니다.")
