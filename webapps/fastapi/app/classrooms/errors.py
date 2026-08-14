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
    "SeatCodeAllocationConflictError",
    "SeatCodeConflictError",
    "SeatCoordinateConflictError",
    "SeatDuplicateError",
    "SeatInactiveForAssignmentError",
    "SeatMigrationCutoverBlockedError",
    "SeatMigrationPostGateError",
    "SeatMigrationPreflightError",
    "SeatMigrationRestoreError",
    "SeatMigrationSnapshotNotFoundError",
    "SeatNotFoundError",
    "SeatRepairInvalidError",
    "SeatRepairNotApprovedError",
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


class SeatCodeConflictError(DomainError):
    """auto 좌석 생성에서 자동 할당한 코드가 이미 예약된 경우 (재시도 대상)."""

    code = "SEAT_CODE_CONFLICT"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("좌석 코드가 이미 사용 중입니다.")


class SeatCoordinateConflictError(DomainError):
    """auto 좌석 생성에서 요청한 (row, column) 좌표가 이미 예약된 경우."""

    code = "SEAT_COORDINATE_CONFLICT"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("해당 좌표에 이미 좌석이 있습니다.")


class SeatCodeAllocationConflictError(DomainError):
    """code conflict 재시도 소진 후 auto 좌석 생성이 최종 실패한 경우."""

    code = "SEAT_CODE_ALLOCATION_CONFLICT"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("좌석 코드를 할당할 수 없습니다.")


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


# ============================================================
# 오프라인 migration 오류
# ============================================================


class SeatMigrationPreflightError(DomainError):
    """preflight 실패 (부분 좌표 등) — zero-write abort."""

    code = "SEAT_MIGRATION_PREFLIGHT_FAILED"
    status_code = 422

    def __init__(self, message: str) -> None:
        super().__init__(message)


class SeatMigrationCutoverBlockedError(DomainError):
    """승인된 암호화 target/KMS 없이 cutover(migration run) 시도."""

    code = "SEAT_MIGRATION_BLOCKED"
    status_code = 422

    def __init__(self, message: str) -> None:
        super().__init__(message)


class SeatMigrationSnapshotNotFoundError(DomainError):
    """요청한 snapshot을 찾을 수 없음."""

    code = "SEAT_MIGRATION_SNAPSHOT_NOT_FOUND"
    status_code = 404

    def __init__(self) -> None:
        super().__init__("요청한 migration snapshot을 찾을 수 없습니다.")


class SeatMigrationRestoreError(DomainError):
    """snapshot 복원 실패 (checksum 불일치·복원 후 검증 실패)."""

    code = "SEAT_MIGRATION_RESTORE_FAILED"
    status_code = 409

    def __init__(self, message: str) -> None:
        super().__init__(message)


class SeatMigrationPostGateError(DomainError):
    """migration 후(post-gate) 검증 실패 — snapshot으로 복원됨."""

    code = "SEAT_MIGRATION_POST_GATE_FAILED"
    status_code = 409

    def __init__(self, message: str) -> None:
        super().__init__(message)


class SeatRepairNotApprovedError(DomainError):
    """수동 repair가 승인되지 않음 (승인 요청 없음·승인 좌표 불일치)."""

    code = "SEAT_REPAIR_NOT_APPROVED"
    status_code = 422

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or "좌석 repair가 승인되지 않았습니다.")


class SeatRepairInvalidError(DomainError):
    """수동 repair 허용 형태 위반 (partial·비양수·사용 중 좌표 등)."""

    code = "SEAT_REPAIR_INVALID"
    status_code = 422

    def __init__(self, message: str) -> None:
        super().__init__(message)
