"""학생 도메인 오류."""

from ..shared.errors import DomainError


class StudentNotFoundError(DomainError):
    """존재하지 않는 학생."""

    code = "STUDENT_NOT_FOUND"
    status_code = 404

    def __init__(self) -> None:
        super().__init__("요청한 학생을 찾을 수 없습니다.")


class StudentDuplicateError(DomainError):
    """학번 중복."""

    code = "STUDENT_DUPLICATE"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("이미 등록된 학번입니다.")


class StudentInputError(DomainError):
    """입력 검증 실패."""

    code = "STUDENT_INPUT_INVALID"
    status_code = 422

    def __init__(self, message: str) -> None:
        super().__init__(message)
