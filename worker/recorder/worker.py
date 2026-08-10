"""카메라별 세그먼트 녹화와 적재를 한 프로세스에서 관리한다.

카메라 대수만큼 프로세스를 띄우지 않는다. stream worker와 같은 구조다.
녹화는 FFmpeg 프로세스가 하고, 이 워커는 그 프로세스의 수명과 적재를 감독한다.
"""

from __future__ import annotations

import logging
import threading

from .errors import SegmentationError
from .retention import RetentionPolicy
from .segmenter import Segmenter
from .uploader import SegmentUploader

logger = logging.getLogger(__name__)


class CameraRecorder:
    """카메라 한 대의 녹화 프로세스와 적재 주기를 담당한다."""

    def __init__(
        self,
        *,
        segmenter: Segmenter,
        uploader: SegmentUploader,
        shutdown_event: threading.Event,
        upload_interval_seconds: float,
    ) -> None:
        self._segmenter = segmenter
        self._uploader = uploader
        self._shutdown_event = shutdown_event
        self._upload_interval_seconds = upload_interval_seconds

        self._uploaded = 0
        self._failed = 0

    @property
    def camera_id(self) -> str:
        return self._segmenter.camera_id

    @property
    def is_recording(self) -> bool:
        return self._segmenter.is_running()

    def run(self) -> None:
        try:
            self._segmenter.start()
        except SegmentationError as error:
            # 시작부터 실패하면 이 카메라는 녹화하지 못한다. 다른 카메라는 계속 돈다.
            logger.error("%s", error)

        try:
            while not self._shutdown_event.is_set():
                self._tick()
                self._shutdown_event.wait(self._upload_interval_seconds)
        finally:
            self._close()

    def _tick(self) -> None:
        if not self._segmenter.is_running():
            try:
                self._segmenter.restart()
            except SegmentationError as error:
                logger.error("%s", error)

        result = self._uploader.upload_pending()
        self._uploaded += result.uploaded
        self._failed += result.failed

    def _close(self) -> None:
        # FFmpeg을 먼저 정상 종료시켜 마지막 세그먼트를 완성한 뒤 올린다.
        # 순서를 바꾸면 마지막 녹화분이 항상 유실된다.
        self._segmenter.stop()
        result = self._uploader.upload_pending()
        self._uploaded += result.uploaded
        self._failed += result.failed
        logger.info(
            "카메라 %s 녹화 종료 — 적재 %d, 실패 %d",
            self.camera_id,
            self._uploaded,
            self._failed,
        )


class RecorderWorker:
    """모든 카메라의 녹화와 보존 기간 정리를 감독한다."""

    def __init__(
        self,
        *,
        recorders: list[CameraRecorder],
        retention: RetentionPolicy,
        retention_interval_seconds: float,
        shutdown_event: threading.Event | None = None,
    ) -> None:
        self._recorders = recorders
        self._retention = retention
        self._retention_interval_seconds = retention_interval_seconds
        self._shutdown_event = shutdown_event or threading.Event()

    @property
    def camera_states(self) -> dict[str, bool]:
        """카메라별 녹화 여부. monitoring 지표와 상태 조회가 쓸 값이다."""
        return {recorder.camera_id: recorder.is_recording for recorder in self._recorders}

    def request_shutdown(self) -> None:
        self._shutdown_event.set()

    def run(self) -> None:
        threads = [
            threading.Thread(
                target=recorder.run, name=f"recorder-{recorder.camera_id}", daemon=True
            )
            for recorder in self._recorders
        ]
        for thread in threads:
            thread.start()
        logger.info("카메라 %d대의 녹화를 시작했다", len(threads))

        try:
            self._supervise()
        finally:
            self._shutdown_event.set()
            for thread in threads:
                thread.join()
            logger.info("recorder worker를 종료했다")

    def _supervise(self) -> None:
        while not self._shutdown_event.is_set():
            self._retention.purge()
            self._shutdown_event.wait(self._retention_interval_seconds)
