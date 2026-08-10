"""파이프라인 루프와 다중 카메라 구성 검증."""

from __future__ import annotations

import threading

from ..camera_reader import ConnectionState, Frame
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
        self.offered = 0

    def offer(self, frame: Frame) -> None:
        self.offered += 1


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
    recorder: SpyRecorder | None = None,
    frame_capture: SpyCapture | None = None,
) -> CameraPipeline:
    return CameraPipeline(
        reader=reader,  # type: ignore[arg-type]
        shutdown_event=shutdown,
        retry_delay_seconds=0,
        recorder=recorder,  # type: ignore[arg-type]
        frame_capture=frame_capture,  # type: ignore[arg-type]
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
    assert capture.offered == 2


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


def build_settings(**overrides: object) -> StreamSettings:
    base = {
        "app_env": "local",
        "stream_sources": "camera-01=rtsp://host/1,camera-02=rtsp://host/2,camera-03=rtsp://host/3",
    }
    return StreamSettings(**{**base, **overrides})  # type: ignore[arg-type]


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
