"""추론 지표 계측 검증. 모델도 Prometheus 서버도 쓰지 않는다.

지표는 전역 레지스트리에 누적되므로 **절대값을 단정하지 않고 증분만 본다.**
테스트 실행 순서에 따라 시작값이 달라지기 때문이다.
"""

from __future__ import annotations

import threading

import numpy as np
from prometheus_client import REGISTRY
from shared.frame_buffer import FrameBuffer

from ..metrics import METRIC_PREFIX
from ..processor import InferenceProcessor
from ..types import Detection, Frame, InferenceResult
from .test_consumer import FakeProcessor, StopAfter, build_consumer, make_captured, make_result


def value(name: str, **labels: str) -> float:
    """전역 레지스트리에서 지표 값을 읽는다. 아직 없으면 0으로 본다."""
    sampled = REGISTRY.get_sample_value(f"{METRIC_PREFIX}{name}", labels or None)
    return 0.0 if sampled is None else float(sampled)


class FakeDetector:
    """정해진 결과나 예외를 내놓는 대역. 모델을 로딩하지 않는다."""

    def __init__(self, outcome: InferenceResult | Exception) -> None:
        self._outcome = outcome

    def detect(self, frame: Frame) -> InferenceResult:
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def make_frame() -> Frame:
    return np.zeros((2, 2, 3), dtype=np.uint8)


def test_추론에_걸린_시간을_기록한다() -> None:
    processor = InferenceProcessor(FakeDetector(make_result(0)))  # type: ignore[arg-type]
    before = value("inference_duration_seconds_count")

    processor.process(make_frame())

    assert value("inference_duration_seconds_count") == before + 1


def test_실패한_추론은_지연_분포에_넣지_않는다() -> None:
    """즉시 터진 호출이 '아주 빠른 추론'으로 섞이면 분포가 거짓말을 한다."""
    processor = InferenceProcessor(FakeDetector(RuntimeError("장치 오류")))  # type: ignore[arg-type]
    before = value("inference_duration_seconds_count")

    try:
        processor.process(make_frame())
    except RuntimeError:
        pass

    assert value("inference_duration_seconds_count") == before


def test_탐지_건수와_신뢰도를_클래스별로_기록한다() -> None:
    result = InferenceResult(
        frame_shape=(2, 2, 3),
        detections=(
            Detection(class_id=0, class_name="person", confidence=0.9, bbox=(0, 0, 1, 1)),
            Detection(class_id=0, class_name="person", confidence=0.5, bbox=(0, 0, 1, 1)),
            Detection(class_id=67, class_name="cell phone", confidence=0.4, bbox=(0, 0, 1, 1)),
        ),
    )
    processor = InferenceProcessor(FakeDetector(result))  # type: ignore[arg-type]
    before_person = value("detections_total", class_name="person")
    before_phone = value("detections_total", class_name="cell phone")
    before_confidence = value("detection_confidence_sum", class_name="person")

    processor.process(make_frame())

    assert value("detections_total", class_name="person") == before_person + 2
    assert value("detections_total", class_name="cell phone") == before_phone + 1
    # 0.9 + 0.5
    assert value("detection_confidence_sum", class_name="person") == before_confidence + 1.4


def test_탐지가_없으면_클래스_지표가_늘지_않는다() -> None:
    processor = InferenceProcessor(FakeDetector(make_result(0)))  # type: ignore[arg-type]
    before = value("detections_total", class_name="person")

    processor.process(make_frame())

    assert value("detections_total", class_name="person") == before


def test_처리한_프레임을_카메라별로_센다() -> None:
    buffer = FrameBuffer(maxsize=4)
    buffer.put(make_captured(0, camera_id="camera-metric-ok"))
    consumer = build_consumer(buffer, FakeProcessor([make_result(1)]), StopAfter(2))

    consumer.run()

    assert value("frames_processed_total", camera_id="camera-metric-ok", result="ok") == 1


def test_실패한_프레임을_따로_센다() -> None:
    buffer = FrameBuffer(maxsize=4)
    buffer.put(make_captured(0, camera_id="camera-metric-fail"))
    consumer = build_consumer(
        buffer, FakeProcessor([RuntimeError("모델 오류")]), StopAfter(2)
    )

    consumer.run()

    assert value("frames_processed_total", camera_id="camera-metric-fail", result="failed") == 1
    assert value("frames_processed_total", camera_id="camera-metric-fail", result="ok") == 0


def test_연속_실패_횟수를_노출한다() -> None:
    """파이프라인이 스스로 멈추기 전에 알아채기 위한 지표다."""
    buffer = FrameBuffer(maxsize=1)
    consumer = build_consumer(
        buffer,
        FakeProcessor([RuntimeError("모델 오류")]),
        threading.Event(),
        max_consecutive_failures=99,
    )

    consumer._process(make_captured(0, camera_id="camera-metric-streak"))
    assert value("inference_consecutive_failures") == 1

    consumer._process(make_captured(1, camera_id="camera-metric-streak"))
    assert value("inference_consecutive_failures") == 2


def test_성공하면_연속_실패_횟수가_0으로_돌아간다() -> None:
    buffer = FrameBuffer(maxsize=1)
    consumer = build_consumer(
        buffer,
        FakeProcessor([RuntimeError("모델 오류"), make_result(1)]),
        threading.Event(),
        max_consecutive_failures=99,
    )

    consumer._process(make_captured(0, camera_id="camera-metric-reset"))
    consumer._process(make_captured(1, camera_id="camera-metric-reset"))

    assert consumer.stats.processed == 1
    assert value("inference_consecutive_failures") == 0
