"""녹화 수명 관리와 종료 처리 검증."""

from __future__ import annotations

import threading

from ..errors import SegmentationError
from ..retention import PurgeResult
from ..uploader import UploadResult
from ..worker import CameraRecorder, RecorderWorker


class FakeSegmenter:
    def __init__(
        self,
        *,
        camera_id: str = "camera-01",
        start_error: bool = False,
        stays_running: bool = True,
    ) -> None:
        self.camera_id = camera_id
        self._start_error = start_error
        self._stays_running = stays_running
        self._running = False
        self.start_calls = 0
        self.restart_calls = 0
        self.stopped = False

    def start(self) -> None:
        self.start_calls += 1
        if self._start_error:
            raise SegmentationError("FFmpeg을 실행하지 못했습니다")
        self._running = self._stays_running

    def is_running(self) -> bool:
        return self._running

    def restart(self) -> None:
        self.restart_calls += 1
        self._running = True

    def stop(self) -> None:
        self.stopped = True
        self._running = False


class FakeUploader:
    def __init__(self, results: list[UploadResult] | None = None) -> None:
        self._results = results or [UploadResult(uploaded=0, failed=0, skipped=0)]
        self.calls = 0
        self.include_in_progress_calls: list[bool] = []

    def upload_pending(self, *, include_in_progress: bool = False) -> UploadResult:
        result = self._results[min(self.calls, len(self._results) - 1)]
        self.calls += 1
        self.include_in_progress_calls.append(include_in_progress)
        return result


class FakeRetention:
    def __init__(self) -> None:
        self.calls = 0

    def purge(self, prefix: str = "") -> PurgeResult:
        self.calls += 1
        return PurgeResult(removed=0, failed=0, inspected=0)


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


def build_recorder(
    segmenter: FakeSegmenter, uploader: FakeUploader, shutdown: threading.Event
) -> CameraRecorder:
    return CameraRecorder(
        segmenter=segmenter,  # type: ignore[arg-type]
        uploader=uploader,  # type: ignore[arg-type]
        shutdown_event=shutdown,
        upload_interval_seconds=0,
    )


class TestCameraRecorder:
    def test_녹화를_시작하고_주기마다_적재한다(self) -> None:
        segmenter = FakeSegmenter()
        uploader = FakeUploader()

        build_recorder(segmenter, uploader, StopAfter(3)).run()

        assert segmenter.start_calls == 1
        # 루프 3회 + 종료 시 1회
        assert uploader.calls == 4

    def test_시작에_실패해도_다른_카메라를_막지_않는다(self) -> None:
        """예외가 스레드 밖으로 나가면 그 카메라만이 아니라 감독 루프가 흔들린다."""
        segmenter = FakeSegmenter(start_error=True)

        build_recorder(segmenter, FakeUploader(), StopAfter(1)).run()

        assert segmenter.stopped

    def test_녹화가_멈추면_재시작한다(self) -> None:
        """FFmpeg이 도중에 죽어도 다음 주기에 다시 띄운다."""
        segmenter = FakeSegmenter(stays_running=False)

        build_recorder(segmenter, FakeUploader(), StopAfter(2)).run()

        assert segmenter.restart_calls >= 1

    def test_종료할_때_FFmpeg을_먼저_끝내고_적재한다(self) -> None:
        """순서를 바꾸면 마지막 세그먼트가 항상 유실된다."""
        order: list[str] = []

        class OrderedSegmenter(FakeSegmenter):
            def stop(self) -> None:
                order.append("stop")
                super().stop()

        class OrderedUploader(FakeUploader):
            def upload_pending(self, *, include_in_progress: bool = False) -> UploadResult:
                order.append("upload")
                return super().upload_pending(include_in_progress=include_in_progress)

        build_recorder(OrderedSegmenter(), OrderedUploader(), StopAfter(0)).run()

        assert order == ["stop", "upload"]

    def test_이미_종료_신호면_녹화를_시작하지_않는다(self) -> None:
        segmenter = FakeSegmenter()
        shutdown = threading.Event()
        shutdown.set()

        build_recorder(segmenter, FakeUploader(), shutdown).run()

        # start는 루프 앞에서 한 번 부르지만 루프는 돌지 않고 곧바로 정리한다.
        assert segmenter.stopped


class TestRecorderWorker:
    def test_카메라별_녹화_상태를_알려준다(self) -> None:
        shutdown = threading.Event()
        recorders = [
            build_recorder(
                FakeSegmenter(camera_id=f"camera-0{index}"), FakeUploader(), shutdown
            )
            for index in (1, 2)
        ]
        worker = RecorderWorker(
            recorders=recorders,
            retention=FakeRetention(),  # type: ignore[arg-type]
            retention_interval_seconds=0,
            shutdown_event=shutdown,
        )

        assert set(worker.camera_states) == {"camera-01", "camera-02"}

    def test_보존_기간_정리를_주기마다_돌린다(self) -> None:
        """감독 루프와 카메라 스레드가 같은 종료 신호를 써야 함께 멈춘다."""
        retention = FakeRetention()
        shutdown = threading.Event()
        segmenter = FakeSegmenter()
        recorders = [build_recorder(segmenter, FakeUploader(), shutdown)]
        worker = RecorderWorker(
            recorders=recorders,
            retention=retention,  # type: ignore[arg-type]
            retention_interval_seconds=0.01,
            shutdown_event=shutdown,
        )

        stopper = threading.Timer(0.2, worker.request_shutdown)
        stopper.start()
        worker.run()
        stopper.cancel()

        assert retention.calls >= 1
        assert segmenter.stopped, "종료 신호가 카메라 스레드까지 닿아야 한다"

    def test_종료_요청은_모든_카메라에_전달된다(self) -> None:
        shutdown = threading.Event()
        worker = RecorderWorker(
            recorders=[],
            retention=FakeRetention(),  # type: ignore[arg-type]
            retention_interval_seconds=0,
            shutdown_event=shutdown,
        )

        worker.request_shutdown()

        assert shutdown.is_set()

    def test_카메라가_없어도_안전하게_돈다(self) -> None:
        shutdown = threading.Event()
        worker = RecorderWorker(
            recorders=[],
            retention=FakeRetention(),  # type: ignore[arg-type]
            retention_interval_seconds=0.01,
            shutdown_event=shutdown,
        )

        stopper = threading.Timer(0.1, worker.request_shutdown)
        stopper.start()
        worker.run()
        stopper.cancel()


def test_종료할_때만_쓰던_세그먼트까지_올린다() -> None:
    """켜지 않으면 마지막 세그먼트가 "가장 최근 파일"로 걸러져 로컬에만 남는다."""
    uploader = FakeUploader()

    build_recorder(FakeSegmenter(), uploader, StopAfter(2)).run()

    # 루프에서는 끄고, 종료 시 한 번만 켠다.
    assert uploader.include_in_progress_calls[:-1] == [False] * (uploader.calls - 1)
    assert uploader.include_in_progress_calls[-1] is True
