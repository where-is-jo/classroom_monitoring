"""중앙 얼굴 분석 서비스 HTTP 어댑터."""

from __future__ import annotations

import httpx

from ..errors import FaceAnalyzerUnavailableError
from ..models import FaceAnalysis


class HttpFaceAnalyzer:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self._url = f"{base_url.rstrip('/')}/internal/face-analysis"
        self._timeout = timeout_seconds

    def analyze(self, enrollment_id: str, frame: bytes) -> FaceAnalysis:
        try:
            response = httpx.post(
                self._url,
                content=frame,
                headers={
                    "Content-Type": "image/jpeg",
                    "X-Face-Enrollment-ID": enrollment_id,
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise FaceAnalyzerUnavailableError() from exc
        return FaceAnalysis(**response.json())

    def finalize(self, enrollment_id: str) -> str:
        self.discard(enrollment_id)
        return "scrfd-10g-detection-only"

    def discard(self, enrollment_id: str) -> None:
        try:
            response = httpx.delete(
                f"{self._url}/sessions/{enrollment_id}",
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise FaceAnalyzerUnavailableError() from exc
