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

from .config import StreamSettings
from .errors import StreamWorkerError
from .rtsp_publisher import RtspPublisher
from .worker import StreamWorker

logger = logging.getLogger(__name__)


def _use_utf8_console() -> None:
    """로그 출력을 UTF-8로 고정한다.

    로그 메시지가 한국어인데 Windows 콘솔 기본 코드페이지(cp949)로 나가면 깨진다.
    실행하는 사람이 PYTHONUTF8을 설정해야만 읽히는 상태로 두지 않는다.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            # 파이프로 넘길 때처럼 바꿀 수 없는 스트림이면 그대로 둔다.
            # 로그가 깨지는 것이 프로세스를 못 띄우는 것보다 낫다.
            pass


def _configure_logging(log_level: str) -> None:
    _use_utf8_console()
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
    )


def _format_validation_error(error: ValidationError) -> str:
    """설정 오류를 변수 이름과 사유만으로 정리한다.

    Pydantic 기본 출력은 입력값을 그대로 붙인다. STREAM_SOURCES에는 카메라 자격
    증명이 들어갈 수 있어 값이 로그에 남으면 안 된다.
    """
    lines = []
    for item in error.errors(include_url=False, include_input=False):
        location = ".".join(str(part) for part in item["loc"])
        # 설정은 환경변수에서 오므로 이름을 환경변수 표기로 보여 준다.
        name = location.upper() if location else "(설정 전체)"
        lines.append(f"  - {name}: {item['msg']}")
    return "\n".join(lines)


def _build_publisher(settings: StreamSettings) -> RtspPublisher | None:
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
    _use_utf8_console()

    try:
        settings = StreamSettings()  # type: ignore[call-arg]  # 값은 환경변수에서 온다
    except ValidationError as error:
        # 어떤 변수가 문제인지만 알린다. 값은 자격 증명일 수 있어 출력하지 않는다.
        logging.basicConfig(level="ERROR")
        logger.error(
            "설정이 올바르지 않아 시작할 수 없다:\n%s", _format_validation_error(error)
        )
        return 1

    _configure_logging(settings.log_level)

    if settings.recording_enabled or settings.frame_capture_enabled:
        logger.warning(
            "영상·프레임 로컬 저장이 켜져 있다. 개발용 임시 수단이며 보존 기간이 "
            "정해져 있지 않다. 저장물을 커밋하지 않는다."
        )

    worker = StreamWorker(settings, publisher=_build_publisher(settings))
    _install_signal_handlers(worker)

    try:
        worker.run()
    except StreamWorkerError as error:
        logger.error("stream worker를 시작하지 못했다: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
