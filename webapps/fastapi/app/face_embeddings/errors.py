"""얼굴 embedding 처리 오류."""

from ..shared.errors import DomainError


class FaceEmbeddingInputError(DomainError):
    code = "FACE_EMBEDDING_INPUT_INVALID"
    status_code = 422

    def __init__(self, message: str) -> None:
        super().__init__(message)


class FaceEmbeddingUnavailableError(DomainError):
    code = "FACE_EMBEDDING_UNAVAILABLE"
    status_code = 503

    def __init__(self, message: str = "얼굴 벡터 생성 서비스를 사용할 수 없습니다.") -> None:
        super().__init__(message)
