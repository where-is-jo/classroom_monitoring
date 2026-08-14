"""얼굴 분석 서버의 embedding API 어댑터."""

import httpx

from ..errors import FaceEmbeddingInputError, FaceEmbeddingUnavailableError
from ..models import SampleEmbedding


class HttpFaceEmbeddingAnalyzer:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self._url = f"{base_url.rstrip('/')}/internal/face-embeddings"
        self._timeout = timeout_seconds

    def create(self, image: bytes) -> SampleEmbedding:
        try:
            response = httpx.post(
                self._url,
                content=image,
                headers={"Content-Type": "image/jpeg"},
                timeout=self._timeout,
            )
        except httpx.HTTPError:
            raise FaceEmbeddingUnavailableError() from None
        if response.status_code == 422:
            raise FaceEmbeddingInputError("얼굴을 정렬하거나 벡터화하지 못한 샘플입니다.")
        try:
            response.raise_for_status()
            value = response.json()
            return SampleEmbedding(
                vector=tuple(float(item) for item in value["vector"]),
                dimension=int(value["dimension"]),
                normalized=bool(value["normalized"]),
                model_name=str(value["model_name"]),
                model_version=str(value["model_version"]),
                preprocessing_version=str(value["preprocessing_version"]),
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            raise FaceEmbeddingUnavailableError() from None
