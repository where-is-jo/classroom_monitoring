"""로컬 USB 카메라를 FFmpeg으로 RTSP 서버에 송출한다.

Jetson이나 CCTV가 직접 RTSP를 내보내는 구성에서는 이 모듈이 필요 없다.
개발 PC에 붙은 USB 카메라를 MediaMTX에 올릴 때만 쓴다.
"""

from __future__ import annotations

import logging
import subprocess
import time
from collections.abc import Callable, Sequence
from typing import Protocol

from .config import mask_url_credentials
from .errors import RtspPublishError

logger = logging.getLogger(__name__)

_TERMINATE_TIMEOUT_SECONDS = 5.0


class ProcessLike(Protocol):
    """subprocess.Popen 중 이 모듈이 쓰는 부분만 추린 것."""

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


ProcessRunner = Callable[[Sequence[str]], ProcessLike]


def _run(command: Sequence[str]) -> ProcessLike:
    return subprocess.Popen(command)


def _input_specifier(input_format: str, device_name: str) -> str:
    """FFmpeg 입력 형식별 장치 지정 문자열을 만든다.

    OS가 아니라 설정값으로 분기한다. dshow만 `video=` 접두사를 요구하고
    v4l2·avfoundation은 장치 경로나 인덱스를 그대로 받는다.
    """
    if input_format == "dshow":
        return f"video={device_name}"
    return device_name


class RtspPublisher:
    """USB 카메라 → FFmpeg → RTSP 송출 프로세스를 관리한다."""

    def __init__(
        self,
        *,
        device_name: str,
        target_url: str,
        input_format: str,
        framerate: int,
        startup_wait_seconds: float,
        process_runner: ProcessRunner = _run,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._device_name = device_name
        self._target_url = target_url
        self._input_format = input_format
        self._framerate = framerate
        self._startup_wait_seconds = startup_wait_seconds
        self._process_runner = process_runner
        self._sleep = sleep
        self._process: ProcessLike | None = None

    @property
    def masked_target_url(self) -> str:
        return mask_url_credentials(self._target_url)

    def build_command(self) -> list[str]:
        """FFmpeg 명령을 만든다. 설정값 확인과 테스트를 위해 분리해 둔다.

        인코딩 옵션은 camera-guides.md의 "H264 corrupted macroblock" 대응에서 온 값이다.
        RTSP는 UDP 대비 손실이 적은 TCP로 보내고, GOP는 프레임률의 2배로 둔다.
        """
        return [
            "ffmpeg",
            "-f",
            self._input_format,
            "-rtbufsize",
            "200M",
            "-framerate",
            str(self._framerate),
            "-i",
            _input_specifier(self._input_format, self._device_name),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-pix_fmt",
            "yuv420p",
            "-g",
            str(self._framerate * 2),
            "-rtsp_transport",
            "tcp",
            "-f",
            "rtsp",
            self._target_url,
        ]

    def start(self) -> None:
        """송출을 시작하고 RTSP 경로가 생길 때까지 기다린다."""
        if self._process is not None:
            return

        try:
            process = self._process_runner(self.build_command())
        except OSError as error:
            # FFmpeg이 설치되지 않았거나 PATH에 없는 경우가 대부분이다.
            raise RtspPublishError(
                f"FFmpeg을 실행하지 못했습니다: {error}"
            ) from error

        self._process = process
        logger.info("RTSP 송출 시작 (%s)", self.masked_target_url)

        # 수신 측이 곧바로 연결하면 아직 경로가 없어 실패한다.
        self._sleep(self._startup_wait_seconds)

        exit_code = process.poll()
        if exit_code is not None:
            self._process = None
            raise RtspPublishError(
                f"FFmpeg이 시작 직후 종료했습니다 (종료 코드 {exit_code}). "
                "장치 이름과 RTSP 서버 기동 여부를 확인합니다."
            )

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def restart(self) -> None:
        logger.warning("RTSP 송출이 멈춰 재시작한다 (%s)", self.masked_target_url)
        self.stop()
        self.start()

    def stop(self) -> None:
        process = self._process
        if process is None:
            return

        self._process = None
        process.terminate()
        try:
            process.wait(timeout=_TERMINATE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            # 종료를 무시하는 FFmpeg이 남으면 장치를 계속 붙들고 있어 다음 실행이 막힌다.
            logger.warning("FFmpeg이 종료되지 않아 강제 종료한다")
            process.kill()
            process.wait(timeout=_TERMINATE_TIMEOUT_SECONDS)
        logger.info("RTSP 송출 종료")
