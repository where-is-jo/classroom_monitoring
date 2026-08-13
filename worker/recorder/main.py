"""recorder worker 진입점.

worker 디렉터리에서 `python -m recorder.main`으로 실행한다.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
from types import FrameType

from pydantic import ValidationError
from shared.config_errors import format_validation_error
from shared.logging_setup import configure_logging, use_utf8_console

from shared.object_storage import ObjectStorage, ObjectStorageError
from shared.object_storage.factory import build_object_storage

from .config import DEFAULT_DATA_DIR, RecorderSettings
from .errors import RecorderError
from .retention import RetentionPolicy
from .segmenter import Segmenter
from .uploader import SegmentUploader
from .worker import CameraRecorder, RecorderWorker

logger = logging.getLogger(__name__)


def build_storage(settings: RecorderSettings) -> ObjectStorage:
    """설정에 맞는 객체 저장소를 만든다. 조립은 shared가 한다."""
    return build_object_storage(settings, local_fallback_dir=DEFAULT_DATA_DIR / "objects")


def build_worker(settings: RecorderSettings, storage: ObjectStorage) -> RecorderWorker:
    """설정에서 워커를 조립한다."""
    shutdown_event = threading.Event()

    recorders = []
    for source in settings.camera_sources:
        segment_dir = settings.recording_segment_dir / source.camera_id
        segmenter = Segmenter(
            source,
            output_dir=segment_dir,
            segment_seconds=settings.recording_segment_seconds,
        )
        uploader = SegmentUploader(
            camera_id=source.camera_id,
            segment_dir=segment_dir,
            storage=storage,
            stale_after_seconds=settings.recording_stale_after_seconds,
        )
        recorders.append(
            CameraRecorder(
                segmenter=segmenter,
                uploader=uploader,
                shutdown_event=shutdown_event,
                upload_interval_seconds=settings.recording_upload_interval_seconds,
            )
        )

    return RecorderWorker(
        recorders=recorders,
        retention=RetentionPolicy(
            storage=storage, retention_days=settings.recording_retention_days
        ),
        retention_interval_seconds=settings.recording_retention_interval_seconds,
        shutdown_event=shutdown_event,
    )


def _install_signal_handlers(worker: RecorderWorker) -> None:
    def handle_signal(signal_number: int, frame: FrameType | None) -> None:
        logger.info(
            "종료 신호(%s)를 받아 정리를 시작한다", signal.Signals(signal_number).name
        )
        worker.request_shutdown()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)


def _warn_about_unsettled_policy(settings: RecorderSettings) -> None:
    """합의되지 않은 정책으로 영상을 저장한다는 사실을 시작할 때 드러낸다."""
    logger.warning(
        "영상 저장을 시작한다. 보존 기간 %d일, 저장소 %s. "
        "저장 범위·보존 기간·접근 권한은 아직 팀 합의 항목이며(결정 0004) "
        "현재 값은 기본값이다. 영상에는 사무실 구성원의 얼굴이 담긴다.",
        settings.recording_retention_days,
        settings.object_storage_backend,
    )


def main() -> int:
    use_utf8_console()

    try:
        settings = RecorderSettings()  # type: ignore[call-arg]  # 값은 환경변수에서 온다
    except ValidationError as error:
        # 어떤 변수가 문제인지만 알린다. 값은 자격 증명일 수 있어 출력하지 않는다.
        logging.basicConfig(level="ERROR")
        logger.error(
            "설정이 올바르지 않아 시작할 수 없다:\n%s", format_validation_error(error)
        )
        return 1

    configure_logging(settings.log_level)
    _warn_about_unsettled_policy(settings)

    try:
        storage = build_storage(settings)
    except (RecorderError, ObjectStorageError) as error:
        # ObjectStorageError는 더 이상 RecorderError가 아니다. shared로 옮기면서
        # 상속이 끊어졌으므로(결정 0011) 여기서 함께 잡지 않으면 MinIO 장애 때
        # 깔끔한 종료 대신 traceback이 그대로 올라온다.
        logger.error("객체 저장소를 준비하지 못했다: %s", error)
        return 1

    worker = build_worker(settings, storage)
    _install_signal_handlers(worker)

    worker.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
