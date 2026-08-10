"""stream worker 진입점.

worker 디렉터리에서 `python -m stream.main`으로 실행한다.
import만으로는 아무 일도 일어나지 않는다. 모듈 최상위에서 장치를 열거나
프로세스를 띄우면 테스트에서 손댈 수 없게 된다.
"""

from __future__ import annotations

import logging
import signal
import sys
from types import FrameType

from pydantic import ValidationError
from shared.config_errors import format_validation_error
from shared.logging_setup import configure_logging, use_utf8_console

from .config import StreamSettings
from .errors import StreamWorkerError
from .rtsp_publisher import RtspPublisher
from .worker import StreamWorker

logger = logging.getLogger(__name__)


def build_publisher(settings: StreamSettings) -> RtspPublisher | None:
    if not settings.rtsp_publish_enabled:
        return None

    # 설정 검증이 두 값의 존재를 이미 보장한다.
    assert settings.rtsp_publish_device_name is not None
    assert settings.rtsp_publish_target_url is not None

    return RtspPublisher(
        device_name=settings.rtsp_publish_device_name,
        target_url=settings.rtsp_publish_target_url.get_secret_value(),
        input_format=settings.rtsp_publish_input_format,
        framerate=settings.rtsp_publish_framerate,
        startup_wait_seconds=settings.stream_startup_wait_seconds,
    )


def _install_signal_handlers(worker: StreamWorker) -> None:
    def handle_signal(signal_number: int, frame: FrameType | None) -> None:
        logger.info("종료 신호(%s)를 받아 정리를 시작한다", signal.Signals(signal_number).name)
        worker.request_shutdown()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)


def main() -> int:
    # 설정을 읽기 전에 실패해도 메시지가 읽히도록 콘솔 인코딩을 먼저 맞춘다.
    use_utf8_console()

    try:
        settings = StreamSettings()  # type: ignore[call-arg]  # 값은 환경변수에서 온다
    except ValidationError as error:
        # 어떤 변수가 문제인지만 알린다. 값은 자격 증명일 수 있어 출력하지 않는다.
        logging.basicConfig(level="ERROR")
        logger.error(
            "설정이 올바르지 않아 시작할 수 없다:\n%s", format_validation_error(error)
        )
        return 1

    configure_logging(settings.log_level)

    if settings.recording_enabled or settings.frame_capture_enabled:
        logger.warning(
            "영상·프레임 로컬 저장이 켜져 있다. 개발용 임시 수단이며 보존 기간이 "
            "정해져 있지 않다. 저장물을 커밋하지 않는다."
        )

    worker = StreamWorker(settings, publisher=build_publisher(settings))
    _install_signal_handlers(worker)

    try:
        worker.run()
    except StreamWorkerError as error:
        logger.error("stream worker를 시작하지 못했다: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
