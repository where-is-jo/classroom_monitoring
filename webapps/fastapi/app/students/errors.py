"""학생 원장 오류."""

from ..shared.errors import DomainError


class StudentInputError(DomainError):
    code = "STUDENT_INPUT_INVALID"
    status_code = 422

    def __init__(self, message: str) -> None:
        super().__init__(message)


class StudentDuplicateError(DomainError):
    code = "STUDENT_DUPLICATE"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("이미 등록된 학생 번호입니다.")


class StudentNotFoundError(DomainError):
    code = "STUDENT_NOT_FOUND"
    status_code = 404

    def __init__(self) -> None:
        super().__init__("학생을 찾을 수 없습니다.")
