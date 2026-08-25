"""수신과 추론의 수명 관리 검증. 실제 카메라도 모델도 쓰지 않는다."""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime

import numpy as np
import pytest
from shared.frame_buffer import FrameBuffer
from shared.types import CapturedFrame

from ..runner import PipelineRunner


def make_captured(sequence: int) -> CapturedFrame:
    return CapturedFrame(
        camera_id="camera-01",
        frame=np.zeros((2, 2, 3), dtype=np.uint8),
        captured_at=datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC),
        sequence=sequence,
    )


class FakeStreamWorker:
    """종료 신호가 올 때까지 프레임을 넣는 StreamWorker 대역."""

    def __init__(
        self,
        buffer: FrameBuffer,
        shutdown: threading.Event,
        *,
        frames: int | None = None,
    ) -> None:
        self._buffer = buffer
        self._shutdown = shutdown
        self._frames = frames
        self.ran = False

    def run(self) -> None:
        self.ran = True
        produced = 0
        while not self._shutdown.is_set():
            if self._frames is not None and produced >= self._frames:
                return
            self._buffer.put(make_captured(produced))
            produced += 1
            time.sleep(0.001)


class FakeConsumer:
    def __init__(self, buffer: FrameBuffer, shutdown: threading.Event) -> None:
        self._buffer = buffer
        self._shutdown = shutdown
        self.ran = False
        self.finished = threading.Event()
        self.consumed = 0

    def run(self) -> None:
        self.ran = True
        try:
            while not self._shutdown.is_set():
                if self._buffer.get_latest(timeout=0.05) is not None:
                    self.consumed += 1
        finally:
            self.finished.set()

    @property
    def stats(self) -> object:
        return _Stats(processed=self.consumed, failed=0)


class _Stats:
    def __init__(self, *, processed: int, failed: int) -> None:
        self.processed = processed
        self.failed = failed


class HangingConsumer:
    """join timeout보다 오래 붙잡는 소비자."""

    def __init__(self) -> None:
        self.release = threading.Event()

    def run(self) -> None:
        self.release.wait(timeout=30)

    @property
    def stats(self) -> object:
        return _Stats(processed=0, failed=0)


def build_runner(
    buffer: FrameBuffer,
    shutdown: threading.Event,
    stream_worker: object,
    consumer: object,
    *,
    join_timeout_seconds: float = 5.0,
) -> PipelineRunner:
    return PipelineRunner(
        stream_worker=stream_worker,  # type: ignore[arg-type]
        consumer=consumer,  # type: ignore[arg-type]
        frame_buffer=buffer,
        shutdown_event=shutdown,
        consumer_join_timeout_seconds=join_timeout_seconds,
    )


def test_수신과_추론을_함께_돌린다() -> None:
    buffer = FrameBuffer(maxsize=1)
    shutdown = threading.Event()
    stream_worker = FakeStreamWorker(buffer, shutdown, frames=20)
    consumer = FakeConsumer(buffer, shutdown)
    runner = build_runner(buffer, shutdown, stream_worker, consumer)

    exit_code = runner.run()

    assert exit_code == 0
    assert stream_worker.ran
    assert consumer.ran


def test_느린_얼굴_소비자가_CCTV_소비자를_막지_않는다() -> None:
    tracking_buffer = FrameBuffer(maxsize=1)
    entry_buffer = FrameBuffer(maxsize=1)
    shutdown = threading.Event()
    tracking_processed = threading.Event()
    entry_processing_started = threading.Event()
    release_entry = threading.Event()

    class TrackingConsumer(FakeConsumer):
        def run(self) -> None:
            captured = self._buffer.get_latest(timeout=0.2)
            if captured is not None:
                self.consumed += 1
                tracking_processed.set()
            self.finished.set()

    class SlowEntryConsumer(FakeConsumer):
        def run(self) -> None:
            if self._buffer.get_latest(timeout=0.2) is not None:
                entry_processing_started.set()
                release_entry.wait(timeout=1)
                self.consumed += 1
            self.finished.set()

    class DualStreamWorker:
        def run(self) -> None:
            entry_buffer.put(make_captured(1))
            assert entry_processing_started.wait(timeout=0.2)
            tracking_buffer.put(make_captured(1))
            assert tracking_processed.wait(timeout=0.2)
            release_entry.set()

    tracking_consumer = TrackingConsumer(tracking_buffer, shutdown)
    entry_consumer = SlowEntryConsumer(entry_buffer, shutdown)
    runner = PipelineRunner(
        stream_worker=DualStreamWorker(),  # type: ignore[arg-type]
        consumer=tracking_consumer,  # type: ignore[arg-type]
        frame_buffer=tracking_buffer,
        additional_consumers=(entry_consumer,),  # type: ignore[arg-type]
        additional_frame_buffers=(entry_buffer,),
        shutdown_event=shutdown,
    )

    assert runner.run() == 0
    assert tracking_consumer.consumed == 1
    assert entry_consumer.consumed == 1


def test_수신이_끝나면_버퍼를_닫아_소비자를_깨운다() -> None:
    """버퍼를 닫지 않으면 소비자가 오지 않을 프레임을 기다린다."""
    buffer = FrameBuffer(maxsize=1)
    shutdown = threading.Event()
    consumer = FakeConsumer(buffer, shutdown)
    runner = build_runner(
        buffer, shutdown, FakeStreamWorker(buffer, shutdown, frames=3), consumer
    )

    runner.run()

    assert buffer.is_closed
    assert consumer.finished.is_set()


def test_종료_요청은_양쪽을_모두_멈춘다() -> None:
    buffer = FrameBuffer(maxsize=1)
    shutdown = threading.Event()
    consumer = FakeConsumer(buffer, shutdown)
    runner = build_runner(
        buffer, shutdown, FakeStreamWorker(buffer, shutdown), consumer
    )

    stopper = threading.Timer(0.1, runner.request_shutdown)
    stopper.start()
    runner.run()
    stopper.cancel()

    assert shutdown.is_set()
    assert buffer.is_closed
    assert consumer.finished.is_set()


def test_종료_요청은_기다리는_소비자를_즉시_깨운다() -> None:
    buffer = FrameBuffer(maxsize=1)
    shutdown = threading.Event()
    runner = build_runner(
        buffer,
        shutdown,
        FakeStreamWorker(buffer, shutdown),
        FakeConsumer(buffer, shutdown),
    )

    runner.request_shutdown()

    assert shutdown.is_set()
    assert buffer.is_closed


def test_수신이_예외로_끝나도_버퍼를_닫는다() -> None:
    class FailingStreamWorker:
        def run(self) -> None:
            raise RuntimeError("카메라를 열지 못했다")

    buffer = FrameBuffer(maxsize=1)
    shutdown = threading.Event()
    consumer = FakeConsumer(buffer, shutdown)
    runner = build_runner(buffer, shutdown, FailingStreamWorker(), consumer)

    try:
        runner.run()
    except RuntimeError:
        pass

    assert buffer.is_closed, "예외 경로에서도 소비자를 깨워야 한다"
    assert consumer.finished.is_set()


def test_소비자가_시간_안에_끝나지_않으면_로그로_알린다(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """진행 중인 추론이 남은 채 종료되는 상황을 조용히 넘기지 않는다."""
    buffer = FrameBuffer(maxsize=1)
    shutdown = threading.Event()
    consumer = HangingConsumer()
    runner = build_runner(
        buffer,
        shutdown,
        FakeStreamWorker(buffer, shutdown, frames=1),
        consumer,
        join_timeout_seconds=0.05,
    )

    try:
        with caplog.at_level(logging.ERROR):
            runner.run()
    finally:
        consumer.release.set()

    assert "끝나지 않았다" in caplog.text
