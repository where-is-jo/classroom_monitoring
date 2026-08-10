from __future__ import annotations

from .model import Yolo8nDetector
from .types import Frame, InferenceResult


class InferenceProcessor:
    """스트림에서 받은 프레임을 inference 모델에 전달하는 경계 컴포넌트."""

    def __init__(self, detector: Yolo8nDetector) -> None:
        self._detector = detector

    def process(self, frame: Frame) -> InferenceResult:
        return self._detector.detect(frame)
