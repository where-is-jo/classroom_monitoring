"""얼굴 등록 도메인 오류."""

from ..shared.errors import DomainError


class EnrollmentNotFoundError(DomainError):
    code = "FACE_ENROLLMENT_NOT_FOUND"
    status_code = 404

    def __init__(self) -> None:
        super().__init__("요청한 얼굴 등록 세션을 찾을 수 없습니다.")


class EnrollmentConflictError(DomainError):
    code = "FACE_ENROLLMENT_CONFLICT"
    status_code = 409

    def __init__(self, message: str = "다른 얼굴 등록 세션이 진행 중입니다.") -> None:
        super().__init__(message)


class ConsentRequiredError(DomainError):
    code = "FACE_CONSENT_REQUIRED"
    status_code = 400

    def __init__(self) -> None:
        super().__init__("외부 동의서 확인이 필요합니다.")


class EnrollmentFrameError(DomainError):
    code = "FACE_FRAME_INVALID"
    status_code = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)


class FaceAnalyzerUnavailableError(DomainError):
    code = "FACE_ANALYZER_UNAVAILABLE"
    status_code = 503

    def __init__(self) -> None:
        super().__init__("얼굴 분석 서버에 연결할 수 없습니다.")
