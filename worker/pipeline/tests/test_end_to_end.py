"""수신 → 샘플링 → 버퍼 → 추론까지 실제 컴포넌트로 잇는 통합 테스트.

대역으로 바꾼 것은 두 곳뿐이다.
- OpenCV VideoCapture: 실제 카메라가 없다
- 추론 모델 호출: 가중치 파일과 ultralytics가 필요하다

그 사이의 CameraReader, 샘플링, FrameBuffer, InferenceConsumer, PipelineRunner는
모두 실제 구현이다.
"""

from __future__ import annotations

import threading

import numpy as np
from inference.consumer import InferenceConsumer
from inference.types import Detection, InferenceResult
from shared.frame_buffer import FrameBuffer
from shared.types import CapturedFrame, Frame
from stream.camera_reader import CameraReader
from stream.config import CameraSource
from stream.worker import CameraPipeline

SOURCE = CameraSource(camera_id="camera-01", rtsp_url="rtsp://localhost:8554/camera")


class EndlessCapture:
    """매번 다른 프레임을 내주는 VideoCapture 대역."""

    def __init__(self) -> None:
        self.read_count = 0

    def isOpened(self) -> bool:
        return True

    def read(self) -> tuple[bool, Frame | None]:
        frame = np.full((4, 4, 3), self.read_count % 256, dtype=np.uint8)
        self.read_count += 1
        return True, frame

    def release(self) -> None:
        pass

    def set(self, prop_id: int, value: float) -> bool:
        return True


class CountingProcessor:
    """추론 모델 대역. 호출된 프레임을 기록한다."""

    def __init__(self) -> None:
        self.frames: list[Frame] = []

    def process(self, frame: Frame) -> InferenceResult:
        self.frames.append(frame)
        return InferenceResult(
            frame_shape=(4, 4, 3),
            detections=(
                Detection(
                    class_id=0, class_name="person", confidence=0.87, bbox=(0, 0, 2, 2)
                ),
            ),
        )


class StopAfter(threading.Event):
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


def test_수신한_프레임이_샘플링을_거쳐_추론까지_닿는다() -> None:
    frame_buffer = FrameBuffer(maxsize=4)
    capture = EndlessCapture()
    reader = CameraReader(
        SOURCE,
        max_retry=1,
        reconnect_delay_seconds=0,
        read_failure_tolerance=1,
        capture_factory=lambda url: capture,
        sleep=lambda seconds: None,
    )
    pipeline = CameraPipeline(
        reader=reader,
        shutdown_event=StopAfter(10),
        retry_delay_seconds=0,
        sample_interval_frames=5,
        frame_buffer=frame_buffer,
    )

    pipeline.run()

    # 10장을 읽어 5프레임마다 1장씩 2장을 골랐다.
    assert capture.read_count == 10
    assert frame_buffer.stats.accepted == 2

    processor = CountingProcessor()
    results: list[tuple[CapturedFrame, InferenceResult]] = []
    consumer = InferenceConsumer(
        frame_buffer=frame_buffer,
        processor=processor,  # type: ignore[arg-type]
        shutdown_event=StopAfter(2),
        poll_timeout_seconds=0.01,
        result_handler=lambda captured, result: results.append((captured, result)),
    )

    consumer.run()

    # 밀려 있던 2장 중 최신 한 장만 추론에 들어간다.
    assert len(processor.frames) == 1
    assert results[0][0].camera_id == "camera-01"
    assert results[0][0].sequence == 5
    assert results[0][1].detections[0].class_name == "person"


def test_추론이_수신보다_느려도_수신이_밀리지_않는다() -> None:
    """생산자는 소비자를 기다리지 않는다. 밀린 프레임은 버려진다."""
    frame_buffer = FrameBuffer(maxsize=1)
    capture = EndlessCapture()
    reader = CameraReader(
        SOURCE,
        max_retry=1,
        reconnect_delay_seconds=0,
        read_failure_tolerance=1,
        capture_factory=lambda url: capture,
        sleep=lambda seconds: None,
    )
    pipeline = CameraPipeline(
        reader=reader,
        shutdown_event=StopAfter(100),
        retry_delay_seconds=0,
        sample_interval_frames=1,
        frame_buffer=frame_buffer,
    )

    pipeline.run()

    stats = frame_buffer.stats
    assert stats.accepted == 100
    assert stats.dropped == 99
    assert len(frame_buffer) == 1

    # 남은 한 장은 가장 마지막에 수신한 프레임이다.
    latest = frame_buffer.get_latest(timeout=0)
    assert latest is not None
    assert latest.sequence == 99


def test_두_카메라가_한_버퍼를_함께_쓴다() -> None:
    frame_buffer = FrameBuffer(maxsize=8)

    for camera_id in ("camera-01", "camera-02"):
        reader = CameraReader(
            CameraSource(camera_id=camera_id, rtsp_url="rtsp://host/x"),
            max_retry=1,
            reconnect_delay_seconds=0,
            read_failure_tolerance=1,
            capture_factory=lambda url: EndlessCapture(),
            sleep=lambda seconds: None,
        )
        CameraPipeline(
            reader=reader,
            shutdown_event=StopAfter(2),
            retry_delay_seconds=0,
            sample_interval_frames=1,
            frame_buffer=frame_buffer,
        ).run()

    assert frame_buffer.stats.accepted == 4
    latest = frame_buffer.get_latest(timeout=0)
    assert latest is not None
    assert latest.camera_id == "camera-02"
