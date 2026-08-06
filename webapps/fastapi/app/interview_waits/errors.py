"""면담 대기 도메인 오류."""

from __future__ import annotations

from ..shared.errors import DomainError


class InterviewWaitNotFoundError(DomainError):
    code = "INTERVIEW_WAIT_NOT_FOUND"
    status_code = 404

    def __init__(self) -> None:
        super().__init__("요청한 면담 대기를 찾을 수 없습니다.")


class InterviewWaitDuplicateError(DomainError):
    code = "INTERVIEW_WAIT_ACTIVE_DUPLICATE"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("같은 직원에 대한 활성 면담 대기가 이미 있습니다.")


class InterviewWaitTransitionError(DomainError):
    code = "INTERVIEW_WAIT_INVALID_TRANSITION"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("현재 면담 대기 상태에서는 요청한 변경을 수행할 수 없습니다.")


class InterviewWaitOperationConflictError(DomainError):
    code = "INTERVIEW_WAIT_OPERATION_CONFLICT"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("같은 작업 식별자가 다른 면담 대기 요청에 사용됐습니다.")


class InterviewWaitConcurrentUpdateError(DomainError):
    code = "INTERVIEW_WAIT_CONCURRENT_UPDATE"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("면담 대기가 동시에 변경됐습니다. 새로고침 후 다시 시도해 주세요.")


class InterviewWaitInputError(DomainError):
    code = "INTERVIEW_WAIT_INPUT_INVALID"
    status_code = 400

    def __init__(self, message: str = "면담 대기 요청 값이 올바르지 않습니다.") -> None:
        super().__init__(message)
