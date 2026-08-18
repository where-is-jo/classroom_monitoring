"""프레임을 모델에 넘기는 경계 컴포넌트. 추론 지표를 재는 자리이기도 하다."""

from __future__ import annotations

import time

from .metrics import DETECTION_CONFIDENCE, DETECTIONS_TOTAL, INFERENCE_DURATION_SECONDS
from .model import Yolo8nDetector
from .types import Frame, InferenceResult


class InferenceProcessor:
    """스트림에서 받은 프레임을 inference 모델에 전달하는 경계 컴포넌트."""

    def __init__(self, detector: Yolo8nDetector) -> None:
        self._detector = detector

    def process(self, frame: Frame) -> InferenceResult:
        """모델을 부르고 걸린 시간과 결과 분포를 기록한다.

        **실패한 호출의 시간은 재지 않는다.** `Histogram.time()`을 쓰면 예외가 나도
        관측이 남아서, 즉시 터진 호출이 "아주 빠른 추론"으로 분포에 섞인다. 실패는
        `consumer.py`가 `frames_processed_total{result="failed"}`로 따로 센다.
        """
        started_at = time.perf_counter()
        result = self._detector.detect(frame)
        INFERENCE_DURATION_SECONDS.observe(time.perf_counter() - started_at)

        for detection in result.detections:
            DETECTIONS_TOTAL.labels(class_name=detection.class_name).inc()
            DETECTION_CONFIDENCE.labels(class_name=detection.class_name).observe(
                detection.confidence
            )
        return result
