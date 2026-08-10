"""여러 카메라를 한 프로세스에서 관리하는 stream worker.

카메라 대수만큼 프로세스를 띄우지 않는다. 애플리케이션 하나가 소스별 스레드를
들고 있으며, 한 카메라의 연결 실패가 다른 카메라를 멈추지 않는다.

OpenCV의 프레임 읽기는 GIL을 놓는 블로킹 호출이라 스레드로 병행된다.
"""

from __future__ import annotations

import logging
import threading

from .camera_reader import CameraReader, ConnectionState
from .config import CameraSource, StreamSettings
from .errors import CameraConnectionError, RtspPublishError
from .frame_capture import FrameCapture
from .rtsp_publisher import RtspPublisher
from .video_recorder import VideoRecorder

logger = logging.getLogger(__name__)

_PUBLISHER_CHECK_INTERVAL_SECONDS = 1.0


class CameraPipeline:
    """카메라 한 대의 수신 루프. 스레드 하나가 이 run()을 돈다."""

    def __init__(
        self,
        *,
        reader: CameraReader,
        shutdown_event: threading.Event,
        retry_delay_seconds: float,
        recorder: VideoRecorder | None = None,
        frame_capture: FrameCapture | None = None,
    ) -> None:
        self._reader = reader
        self._shutdown_event = shutdown_event
        self._retry_delay_seconds = retry_delay_seconds
        self._recorder = recorder
        self._frame_capture = frame_capture

    @property
    def camera_id(self) -> str:
        return self._reader.camera_id

    @property
    def state(self) -> ConnectionState:
        return self._reader.state

    def run(self) -> None:
        try:
            while not self._shutdown_event.is_set():
                try:
                    self._process_once()
                except CameraConnectionError as error:
                    # 장치는 자주 끊긴다. 프로세스를 죽이지 않고 상태로 남긴 뒤
                    # 잠시 쉬었다가 다시 연결한다. 실패를 감추지는 않는다.
                    logger.error("카메라 %s: %s", self.camera_id, error)
                    self._shutdown_event.wait(self._retry_delay_seconds)
        finally:
            self._close()

    def _process_once(self) -> None:
        if self._reader.state is not ConnectionState.CONNECTED:
            self._reader.connect()

        frame = self._reader.read()
        if frame is None:
            return

        if self._recorder is not None:
            self._recorder.write(frame)
        if self._frame_capture is not None:
            self._frame_capture.offer(frame)

    def _close(self) -> None:
        self._reader.close()
        if self._recorder is not None:
            self._recorder.close()


class StreamWorker:
    """설정에 있는 모든 카메라의 파이프라인과 RTSP 송출을 감독한다."""

    def __init__(
        self,
        settings: StreamSettings,
        *,
        publisher: RtspPublisher | None = None,
    ) -> None:
        self._settings = settings
        self._publisher = publisher
        self._shutdown_event = threading.Event()
        self._pipelines = [
            self._build_pipeline(source) for source in settings.camera_sources
        ]

    @property
    def camera_states(self) -> dict[str, ConnectionState]:
        """카메라별 연결 상태. monitoring 지표와 상태 조회가 쓸 값이다."""
        return {pipeline.camera_id: pipeline.state for pipeline in self._pipelines}

    def request_shutdown(self) -> None:
        self._shutdown_event.set()

    def run(self) -> None:
        if self._publisher is not None:
            self._publisher.start()

        threads = [
            threading.Thread(
                target=pipeline.run, name=f"camera-{pipeline.camera_id}", daemon=True
            )
            for pipeline in self._pipelines
        ]
        for thread in threads:
            thread.start()
        logger.info("카메라 %d대의 수신을 시작했다", len(threads))

        try:
            self._supervise()
        finally:
            self._shutdown_event.set()
            for thread in threads:
                thread.join()
            if self._publisher is not None:
                self._publisher.stop()
            logger.info("stream worker를 종료했다")

    def _supervise(self) -> None:
        while not self._shutdown_event.is_set():
            if self._publisher is not None and not self._publisher.is_running():
                try:
                    self._publisher.restart()
                except RtspPublishError as error:
                    # 송출이 죽으면 모든 카메라가 끊긴다. 복구를 계속 시도하되
                    # 실패를 로그로 드러낸다.
                    logger.error("RTSP 송출 재시작 실패: %s", error)
            self._shutdown_event.wait(_PUBLISHER_CHECK_INTERVAL_SECONDS)

    def _build_pipeline(self, source: CameraSource) -> CameraPipeline:
        settings = self._settings

        recorder = None
        if settings.recording_enabled:
            recorder = VideoRecorder(
                camera_id=source.camera_id,
                output_dir=settings.recording_output_dir,
                fps=settings.recording_fps,
                segment_seconds=settings.recording_segment_seconds,
            )

        frame_capture = None
        if settings.frame_capture_enabled:
            frame_capture = FrameCapture(
                camera_id=source.camera_id,
                output_dir=settings.frame_capture_output_dir,
                interval_frames=settings.frame_sample_interval_frames,
            )

        reader = CameraReader(
            source,
            max_retry=settings.stream_reconnect_max_retry,
            reconnect_delay_seconds=settings.stream_reconnect_delay_seconds,
            read_failure_tolerance=settings.stream_read_failure_tolerance,
        )
        return CameraPipeline(
            reader=reader,
            shutdown_event=self._shutdown_event,
            retry_delay_seconds=settings.stream_reconnect_delay_seconds,
            recorder=recorder,
            frame_capture=frame_capture,
        )
