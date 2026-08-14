"""파이프라인 루프, 샘플링, 버퍼 공급, 다중 카메라 구성 검증."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

from shared.frame_buffer import FrameBuffer
from shared.types import Frame

from ..camera_reader import ConnectionState
from ..config import StreamSettings
from ..errors import CameraConnectionError
from ..worker import CameraPipeline, StreamWorker
from .conftest import make_frame


class FakeReader:
    """CameraReader 대역. 정해진 순서대로 프레임·실패를 내놓는다."""

    def __init__(self, script: list[object], *, camera_id: str = "camera-01") -> None:
        self._script = script
        self._index = 0
        self.camera_id = camera_id
        self.state = ConnectionState.IDLE
        self.connect_count = 0
        self.closed = False

    def connect(self) -> None:
        self.connect_count += 1
        self.state = ConnectionState.CONNECTED

    def read(self) -> Frame | None:
        if self._index >= len(self._script):
            raise StopIteration("대본이 끝났다")
        step = self._script[self._index]
        self._index += 1
        if isinstance(step, Exception):
            self.state = ConnectionState.FAILED
            raise step
        return step  # type: ignore[return-value]

    def close(self) -> None:
        self.closed = True
        self.state = ConnectionState.STOPPED


class SpyRecorder:
    def __init__(self) -> None:
        self.written = 0
        self.closed = False

    def write(self, frame: Frame) -> None:
        self.written += 1

    def close(self) -> None:
        self.closed = True


class SpyCapture:
    def __init__(self) -> None:
        self.saved = 0

    def save(self, frame: Frame) -> Path | None:
        self.saved += 1
        return None


class StopAfter(threading.Event):
    """정해진 횟수만큼 확인한 뒤 종료 신호를 켠다. 무한 루프를 끊기 위한 것이다."""

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


def build_pipeline(
    reader: FakeReader,
    shutdown: threading.Event,
    *,
    sample_interval_frames: int = 1,
    recorder: SpyRecorder | None = None,
    frame_capture: SpyCapture | None = None,
    frame_buffer: FrameBuffer | None = None,
) -> CameraPipeline:
    return CameraPipeline(
        reader=reader,  # type: ignore[arg-type]
        shutdown_event=shutdown,
        retry_delay_seconds=0,
        sample_interval_frames=sample_interval_frames,
        recorder=recorder,  # type: ignore[arg-type]
        frame_capture=frame_capture,  # type: ignore[arg-type]
        frame_buffer=frame_buffer,
        now=lambda: datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC),
    )


def test_프레임을_저장소와_샘플러에_모두_넘긴다() -> None:
    reader = FakeReader([make_frame(), make_frame()])
    recorder = SpyRecorder()
    capture = SpyCapture()
    pipeline = build_pipeline(
        reader, StopAfter(2), recorder=recorder, frame_capture=capture
    )

    pipeline.run()

    assert recorder.written == 2
    assert capture.saved == 2


def test_None_프레임은_저장하지_않는다() -> None:
    reader = FakeReader([None, make_frame()])
    recorder = SpyRecorder()
    pipeline = build_pipeline(reader, StopAfter(2), recorder=recorder)

    pipeline.run()

    assert recorder.written == 1


def test_연결이_끊겨도_루프가_죽지_않고_다시_연결한다() -> None:
    reader = FakeReader(
        [make_frame(), CameraConnectionError("연결 끊김"), make_frame()]
    )
    pipeline = build_pipeline(reader, StopAfter(3))

    pipeline.run()

    # 최초 1회 + 실패 후 재연결 1회
    assert reader.connect_count == 2


def test_종료하면_리더와_저장소를_정리한다() -> None:
    reader = FakeReader([make_frame()])
    recorder = SpyRecorder()
    pipeline = build_pipeline(reader, StopAfter(1), recorder=recorder)

    pipeline.run()

    assert reader.closed
    assert recorder.closed


def test_이미_종료_신호면_한_번도_읽지_않는다() -> None:
    reader = FakeReader([make_frame()])
    shutdown = threading.Event()
    shutdown.set()
    pipeline = build_pipeline(reader, shutdown)

    pipeline.run()

    assert reader.connect_count == 0
    assert reader.closed


class TestSampling:
    def test_원본_영상은_샘플링하지_않고_모두_담는다(self) -> None:
        """녹화에서 프레임을 건너뛰면 영상이 끊겨 보인다."""
        reader = FakeReader([make_frame() for _ in range(6)])
        recorder = SpyRecorder()
        pipeline = build_pipeline(
            reader, StopAfter(6), sample_interval_frames=3, recorder=recorder
        )

        pipeline.run()

        assert recorder.written == 6

    def test_주기에_해당하는_프레임만_버퍼에_넣는다(self) -> None:
        reader = FakeReader([make_frame() for _ in range(6)])
        buffer = FrameBuffer(maxsize=10)
        pipeline = build_pipeline(
            reader, StopAfter(6), sample_interval_frames=3, frame_buffer=buffer
        )

        pipeline.run()

        # 0, 3번 프레임만 고른다.
        assert buffer.stats.accepted == 2

    def test_저장기와_버퍼가_같은_프레임을_받는다(self) -> None:
        """따로 세면 디스크의 학습용 이미지와 추론에 들어간 프레임이 어긋난다."""
        reader = FakeReader([make_frame() for _ in range(9)])
        capture = SpyCapture()
        buffer = FrameBuffer(maxsize=10)
        pipeline = build_pipeline(
            reader,
            StopAfter(9),
            sample_interval_frames=4,
            frame_capture=capture,
            frame_buffer=buffer,
        )

        pipeline.run()

        assert capture.saved == buffer.stats.accepted == 3

    def test_버퍼에_카메라와_프레임_번호를_함께_넣는다(self) -> None:
        reader = FakeReader([make_frame() for _ in range(3)], camera_id="camera-02")
        buffer = FrameBuffer(maxsize=10)
        pipeline = build_pipeline(
            reader, StopAfter(3), sample_interval_frames=2, frame_buffer=buffer
        )

        pipeline.run()

        captured = buffer.get_latest(timeout=0)
        assert captured is not None
        assert captured.camera_id == "camera-02"
        assert captured.sequence == 2

    def test_버퍼가_없으면_아무것도_하지_않는다(self) -> None:
        reader = FakeReader([make_frame()])
        pipeline = build_pipeline(reader, StopAfter(1), frame_buffer=None)

        pipeline.run()  # 예외가 나지 않아야 한다


def build_settings(**overrides: object) -> StreamSettings:
    base = {
        "app_env": "local",
        "stream_sources": "camera-01=rtsp://host/1,camera-02=rtsp://host/2,camera-03=rtsp://host/3",
    }
    # _env_file=None으로 stream/.env.*를 무시한다. 값은 base와 overrides로만 결정한다.
    return StreamSettings(_env_file=None, **{**base, **overrides})  # type: ignore[arg-type]


class TestStreamWorker:
    def test_카메라_대수만큼_파이프라인을_만든다(self) -> None:
        worker = StreamWorker(build_settings())

        assert set(worker.camera_states) == {"camera-01", "camera-02", "camera-03"}

    def test_시작_전_상태는_idle이다(self) -> None:
        worker = StreamWorker(build_settings())

        assert all(
            state is ConnectionState.IDLE for state in worker.camera_states.values()
        )

    def test_저장이_꺼져_있으면_저장_컴포넌트를_붙이지_않는다(self) -> None:
        worker = StreamWorker(build_settings())

        pipeline = worker._pipelines[0]
        assert pipeline._recorder is None
        assert pipeline._frame_capture is None

    def test_저장을_켜면_카메라마다_저장_컴포넌트가_생긴다(self) -> None:
        worker = StreamWorker(
            build_settings(recording_enabled=True, frame_capture_enabled=True)
        )

        pipeline = worker._pipelines[0]
        assert pipeline._recorder is not None
        assert pipeline._frame_capture is not None

    def test_종료_요청은_모든_파이프라인에_전달된다(self) -> None:
        worker = StreamWorker(build_settings())

        worker.request_shutdown()

        assert worker._shutdown_event.is_set()

    def test_모든_카메라가_같은_버퍼를_공유한다(self) -> None:
        buffer = FrameBuffer(maxsize=1)
        worker = StreamWorker(build_settings(), frame_buffer=buffer)

        assert all(
            pipeline._frame_buffer is buffer for pipeline in worker._pipelines
        )

    def test_종료_신호를_주입하면_그것을_쓴다(self) -> None:
        """추론이 치명적으로 실패하면 수신도 함께 멈춰야 한다."""
        shutdown = threading.Event()
        worker = StreamWorker(build_settings(), shutdown_event=shutdown)

        shutdown.set()

        assert worker._shutdown_event is shutdown
