"""stream + inference 조립 진입점.

worker 디렉터리에서 `python -m pipeline.main`으로 실행한다.
설정을 읽고 객체를 조립하는 코드는 여기 한 곳에만 둔다. 워커 안에서 서로를
직접 조립하면 나중에 추론을 별도 프로세스로 뗄 때 고칠 곳이 흩어진다.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
from types import FrameType

from inference.config import DEFAULT_DATA_DIR as INFERENCE_DATA_DIR
from inference.config import InferenceSettings
from inference.consumer import InferenceConsumer, ResultHandler, log_result
from inference.handler import FastAPIResultHandler
from inference.model import Yolo8nDetector
from inference.processor import InferenceProcessor
from inference.snapshot import SnapshotResultHandler
from pydantic import ValidationError
from shared.config_errors import format_validation_error
from shared.object_storage.factory import build_object_storage
from shared.frame_buffer import FrameBuffer
from shared.logging_setup import configure_logging, use_utf8_console
from stream.config import StreamSettings
from stream.errors import StreamWorkerError
from stream.main import build_publisher
from stream.worker import StreamWorker

from .config import PIPELINE_ENV_FILE, PipelineSettings
from .runner import PipelineRunner

logger = logging.getLogger(__name__)


def build_result_handler(
    settings: InferenceSettings,
    *,
    fastapi_url: str | None = None,
) -> ResultHandler:
    """탐지 결과를 무엇으로 받을지 정한다.

    스냅샷이 꺼져 있으면 저장소를 만들지 않는다. MinIO 접속 정보 없이도 파이프라인이
    돌아야 하고, 저장은 명시적으로 켜는 것이라는 규칙(결정 0011)에 맞춘다.

    `fastapi_url`이 주어지면(FASTAPI_URL을 설정한 경우) HTTP 전송 핸들러로 감싼다.
    감싼 핸들러는 기존 로그·스냅샷 동작을 먼저 수행하고 그다음 전송하므로, 전송이
    실패해도 탐지 기록은 남는다.
    """
    if not settings.snapshot_enabled:
        handler: ResultHandler = log_result
    else:
        storage = build_object_storage(
            settings, local_fallback_dir=INFERENCE_DATA_DIR / "snapshots"
        )
        logger.info(
            "탐지 스냅샷 적재를 켠다. 긴 변 %dpx, 품질 %d, 카메라당 최소 간격 %.0f초. "
            "영상 원본은 저장하지 않는다(결정 0011).",
            settings.snapshot_max_long_side_px,
            settings.snapshot_jpeg_quality,
            settings.snapshot_min_interval_seconds,
        )
        handler = SnapshotResultHandler(
            storage=storage,
            min_interval_seconds=settings.snapshot_min_interval_seconds,
            max_long_side_px=settings.snapshot_max_long_side_px,
            jpeg_quality=settings.snapshot_jpeg_quality,
        )

    if fastapi_url is None:
        return handler

    logger.info("탐지 결과를 FastAPI(%s)로 전송한다.", fastapi_url)
    return FastAPIResultHandler(fastapi_url, inner=handler)


def build_runner(
    *,
    stream_settings: StreamSettings,
    inference_settings: InferenceSettings,
    pipeline_settings: PipelineSettings,
) -> PipelineRunner:
    """설정에서 파이프라인을 조립한다. 모델은 여기서 한 번만 로딩한다."""
    shutdown_event = threading.Event()
    frame_buffer = FrameBuffer(maxsize=pipeline_settings.frame_buffer_maxsize)

    # 모델 로딩은 프로세스 시작 시 1회다. 프레임마다 불러오면 추론이 멈춘다.
    detector = Yolo8nDetector(
        model_path=inference_settings.model_path,
        device=inference_settings.inference_device,
        confidence_threshold=inference_settings.inference_confidence_threshold,
    )
    # FASTAPI_URL은 .env·환경변수로 명시해야만 전송을 켠다. 필드에 기본값이 있어
    # 값이 채워져 있는 것만으로는 "설정했다"를 판단할 수 없다. 명시 여부는 pydantic이
    # 기록한 model_fields_set으로 본다. 빈 문자열로 끈 것도 설정으로 치지 않는다.
    fastapi_url: str | None = None
    if "fastapi_url" in pipeline_settings.model_fields_set:
        candidate = pipeline_settings.fastapi_url.strip()
        if candidate:
            fastapi_url = candidate

    consumer = InferenceConsumer(
        frame_buffer=frame_buffer,
        processor=InferenceProcessor(detector),
        shutdown_event=shutdown_event,
        poll_timeout_seconds=pipeline_settings.inference_poll_timeout_seconds,
        max_consecutive_failures=pipeline_settings.inference_max_consecutive_failures,
        result_handler=build_result_handler(
            inference_settings, fastapi_url=fastapi_url
        ),
    )
    stream_worker = StreamWorker(
        stream_settings,
        publisher=build_publisher(stream_settings),
        frame_buffer=frame_buffer,
        shutdown_event=shutdown_event,
    )
    return PipelineRunner(
        stream_worker=stream_worker,
        consumer=consumer,
        frame_buffer=frame_buffer,
        shutdown_event=shutdown_event,
    )


def _install_signal_handlers(runner: PipelineRunner) -> None:
    def handle_signal(signal_number: int, frame: FrameType | None) -> None:
        logger.info(
            "종료 신호(%s)를 받아 정리를 시작한다", signal.Signals(signal_number).name
        )
        runner.request_shutdown()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)


def main() -> int:
    use_utf8_console()

    try:
        # 조립 실행에서는 워커별 .env.*가 아니라 pipeline/.env.{APP_ENV} 하나만 읽는다.
        # 일반 설정·판정 기준값(config/settings.yml)은 각자 자기 디렉터리 것을 그대로 읽는다.
        stream_settings = StreamSettings(_env_file=PIPELINE_ENV_FILE)  # type: ignore[call-arg]
        inference_settings = InferenceSettings(_env_file=PIPELINE_ENV_FILE)  # type: ignore[call-arg]
        pipeline_settings = PipelineSettings(_env_file=PIPELINE_ENV_FILE)  # type: ignore[call-arg]
    except ValidationError as error:
        logging.basicConfig(level="ERROR")
        logger.error(
            "설정이 올바르지 않아 시작할 수 없다:\n%s", format_validation_error(error)
        )
        return 1

    configure_logging(stream_settings.log_level)

    if stream_settings.recording_enabled or stream_settings.frame_capture_enabled:
        logger.warning(
            "영상·프레임 로컬 저장이 켜져 있다. 개발용 임시 수단이며 보존 기간이 "
            "정해져 있지 않다. 저장물을 커밋하지 않는다."
        )

    try:
        runner = build_runner(
            stream_settings=stream_settings,
            inference_settings=inference_settings,
            pipeline_settings=pipeline_settings,
        )
    except (ImportError, OSError) as error:
        # ultralytics 미설치나 가중치 파일을 찾지 못한 경우가 대부분이다.
        logger.error("추론 모델을 준비하지 못했다: %s", error)
        return 1

    _install_signal_handlers(runner)

    try:
        return runner.run()
    except StreamWorkerError as error:
        logger.error("파이프라인을 시작하지 못했다: %s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
