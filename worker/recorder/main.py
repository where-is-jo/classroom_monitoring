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

from .adapters.local import LocalObjectStorage
from .adapters.minio_storage import MinioObjectStorage, build_minio_client
from .config import RecorderSettings
from .errors import RecorderError
from .ports import ObjectStorage
from .retention import RetentionPolicy
from .segmenter import Segmenter
from .uploader import SegmentUploader
from .worker import CameraRecorder, RecorderWorker

logger = logging.getLogger(__name__)


def build_storage(settings: RecorderSettings) -> ObjectStorage:
    """설정에 맞는 객체 저장소를 만든다. SDK를 아는 곳은 어댑터뿐이다."""
    if settings.object_storage_backend == "local":
        logger.warning(
            "객체 저장소가 로컬 디렉터리다. 개발용이며 운영 보관 수단이 아니다: %s",
            settings.object_storage_local_dir,
        )
        return LocalObjectStorage(settings.object_storage_local_dir)

    # 검증이 세 값의 존재를 이미 보장한다.
    assert settings.object_storage_endpoint is not None
    assert settings.object_storage_access_key is not None
    assert settings.object_storage_secret_key is not None

    client = build_minio_client(
        endpoint=settings.object_storage_endpoint,
        access_key=settings.object_storage_access_key.get_secret_value(),
        secret_key=settings.object_storage_secret_key.get_secret_value(),
        secure=settings.object_storage_secure,
    )
    storage = MinioObjectStorage(client, settings.object_storage_bucket)
    # 적재할 때가 되어서야 버킷이 없는 것을 알면 이미 세그먼트가 쌓여 있다.
    storage.ensure_bucket()
    return storage


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
    except RecorderError as error:
        logger.error("객체 저장소를 준비하지 못했다: %s", error)
        return 1

    worker = build_worker(settings, storage)
    _install_signal_handlers(worker)

    worker.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
