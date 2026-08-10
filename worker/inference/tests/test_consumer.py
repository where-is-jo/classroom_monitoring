"""추론 소비자 루프 검증. 실제 모델을 쓰지 않는다."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

import numpy as np
from shared.frame_buffer import FrameBuffer
from shared.types import CapturedFrame, Frame

from ..consumer import InferenceConsumer
from ..types import Detection, InferenceResult


def make_captured(sequence: int, *, camera_id: str = "camera-01") -> CapturedFrame:
    return CapturedFrame(
        camera_id=camera_id,
        frame=np.zeros((2, 2, 3), dtype=np.uint8),
        captured_at=datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC),
        sequence=sequence,
    )


def make_result(count: int = 1) -> InferenceResult:
    detections = tuple(
        Detection(class_id=0, class_name="person", confidence=0.9, bbox=(0, 0, 1, 1))
        for _ in range(count)
    )
    return InferenceResult(frame_shape=(2, 2, 3), detections=detections)


class FakeProcessor:
    """정해진 순서대로 결과나 예외를 내놓는다."""

    def __init__(self, script: list[object]) -> None:
        self._script = script
        self._index = 0
        self.calls: list[Frame] = []

    def process(self, frame: Frame) -> InferenceResult:
        self.calls.append(frame)
        step = self._script[min(self._index, len(self._script) - 1)]
        self._index += 1
        if isinstance(step, Exception):
            raise step
        return step  # type: ignore[return-value]


class StopAfter(threading.Event):
    """정해진 횟수만큼 확인한 뒤 종료 신호를 켠다."""

    def __init__(self, checks: int) -> None:
        super().__init__()
        self._remaining = checks

    def is_set(self) -> bool:
        if super().is_set():
            return True
        if self._remaining <= 0:
            return True
        self._remaining -= 1
        return False


def build_consumer(
    buffer: FrameBuffer,
    processor: FakeProcessor,
    shutdown: threading.Event,
    *,
    max_consecutive_failures: int = 3,
    results: list[tuple[CapturedFrame, InferenceResult]] | None = None,
) -> InferenceConsumer:
    def handler(captured: CapturedFrame, result: InferenceResult) -> None:
        if results is not None:
            results.append((captured, result))

    return InferenceConsumer(
        frame_buffer=buffer,
        processor=processor,  # type: ignore[arg-type]
        shutdown_event=shutdown,
        poll_timeout_seconds=0.01,
        max_consecutive_failures=max_consecutive_failures,
        result_handler=handler,
    )


def test_버퍼의_프레임을_추론해_결과를_넘긴다() -> None:
    buffer = FrameBuffer(maxsize=4)
    buffer.put(make_captured(0))
    processor = FakeProcessor([make_result(2)])
    results: list[tuple[CapturedFrame, InferenceResult]] = []
    consumer = build_consumer(buffer, processor, StopAfter(2), results=results)

    consumer.run()

    assert consumer.stats.processed == 1
    assert len(results) == 1
    assert results[0][0].sequence == 0
    assert len(results[0][1].detections) == 2


def test_가장_최근_프레임만_추론한다() -> None:
    """밀린 프레임을 추론하면 결과가 계속 과거를 가리킨다."""
    buffer = FrameBuffer(maxsize=10)
    for sequence in range(5):
        buffer.put(make_captured(sequence))
    processor = FakeProcessor([make_result()])
    results: list[tuple[CapturedFrame, InferenceResult]] = []
    consumer = build_consumer(buffer, processor, StopAfter(1), results=results)

    consumer.run()

    assert len(processor.calls) == 1
    assert results[0][0].sequence == 4


def test_버퍼가_비어_있으면_아무것도_하지_않는다() -> None:
    buffer = FrameBuffer(maxsize=1)
    processor = FakeProcessor([make_result()])
    consumer = build_consumer(buffer, processor, StopAfter(3))

    consumer.run()

    assert processor.calls == []
    assert consumer.stats.processed == 0


def test_추론이_실패해도_루프가_계속_돈다() -> None:
    buffer = FrameBuffer(maxsize=10)
    buffer.put(make_captured(0))
    processor = FakeProcessor([RuntimeError("모델 오류"), make_result()])
    shutdown = StopAfter(4)
    consumer = build_consumer(buffer, processor, shutdown)

    def feed() -> None:
        buffer.put(make_captured(1))

    consumer._process(make_captured(0))  # 첫 실패
    feed()
    consumer.run()

    assert consumer.stats.failed == 1
    assert consumer.stats.processed >= 1


def test_성공하면_연속_실패_횟수가_초기화된다() -> None:
    buffer = FrameBuffer(maxsize=1)
    processor = FakeProcessor([RuntimeError("일시적"), make_result()])
    shutdown = threading.Event()
    consumer = build_consumer(buffer, processor, shutdown, max_consecutive_failures=2)

    consumer._process(make_captured(0))
    consumer._process(make_captured(1))
    consumer._process(make_captured(2))

    assert not shutdown.is_set(), "중간에 성공했으면 멈추지 않아야 한다"
    assert consumer.stats.failed == 1
    assert consumer.stats.processed == 2


def test_연속_실패가_한계를_넘으면_파이프라인을_멈춘다() -> None:
    """계속 실패하는 상태로 도는 것은 프레임만 버리는 것과 같다."""
    buffer = FrameBuffer(maxsize=1)
    processor = FakeProcessor([RuntimeError("모델을 못 불렀다")])
    shutdown = threading.Event()
    consumer = build_consumer(buffer, processor, shutdown, max_consecutive_failures=2)

    consumer._process(make_captured(0))
    assert not shutdown.is_set()

    consumer._process(make_captured(1))

    assert shutdown.is_set(), "종료 신호를 켜야 수신도 함께 멈춘다"
    assert consumer.stats.failed == 2


def test_버퍼가_닫히면_루프를_빠져나온다() -> None:
    buffer = FrameBuffer(maxsize=1)
    processor = FakeProcessor([make_result()])
    shutdown = threading.Event()
    consumer = InferenceConsumer(
        frame_buffer=buffer,
        processor=processor,  # type: ignore[arg-type]
        shutdown_event=shutdown,
        poll_timeout_seconds=10.0,
    )
    thread = threading.Thread(target=consumer.run)
    thread.start()

    shutdown.set()
    buffer.close()
    thread.join(timeout=3.0)

    assert not thread.is_alive(), "종료 신호와 버퍼 닫힘으로 즉시 빠져나와야 한다"


def test_기본_결과_처리는_예외를_내지_않는다() -> None:
    """기본 핸들러는 로그만 남긴다. 여기서 업무 의미를 붙이지 않는다."""
    from ..consumer import log_result

    log_result(make_captured(0), make_result(2))
    log_result(make_captured(1), InferenceResult(frame_shape=(2, 2, 3), detections=()))
