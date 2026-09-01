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


class CameraFrameUnavailableError(DomainError):
    """카메라에서 현재 프레임을 받지 못했다.

    "카메라를 못 봤다"는 사실이지 "학생이 없다"는 판정이 아니다(AGENTS.md 5번).
    502를 쓰는 이유는 실패의 원인이 이 앱이 아니라 upstream 카메라에 있기 때문이다.
    """

    code = "CAMERA_FRAME_UNAVAILABLE"
    status_code = 502

    def __init__(self, message: str) -> None:
        super().__init__(message)
