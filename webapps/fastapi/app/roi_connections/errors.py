"""ROI 연결 기능 오류."""

from ..shared.errors import DomainError


class RoiConnectionNotFoundError(DomainError):
    code = "ROI_CONNECTION_NOT_FOUND"
    status_code = 404

    def __init__(self, message: str) -> None:
        super().__init__(message)


class RoiConnectionInputError(DomainError):
    code = "ROI_CONNECTION_INPUT_INVALID"
    status_code = 422

    def __init__(self, message: str) -> None:
        super().__init__(message)


class RoiConnectionConflictError(DomainError):
    code = "ROI_CONNECTION_CONFLICT"
    status_code = 409

    def __init__(self, message: str) -> None:
        super().__init__(message)
