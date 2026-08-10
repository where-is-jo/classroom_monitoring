"""RTSP 스트림을 세그먼트 파일로 떨구는 FFmpeg 프로세스를 관리한다.

**프레임을 디코딩하지 않는다.** `-c copy`로 받은 그대로 파일에 쓴다. 저장 때문에
CPU를 쓰면 추론 경로가 느려지고, 재인코딩은 원본을 열화시킨다. recorder가
stream worker의 프레임이 아니라 MediaMTX에서 직접 받는 이유가 이것이다.
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from shared.camera_sources import CameraSource

from .errors import SegmentationError

logger = logging.getLogger(__name__)

_TERMINATE_TIMEOUT_SECONDS = 10.0

# FFmpeg이 strftime으로 만드는 파일 이름. 업로드할 때 녹화 시각을 여기서 되읽는다.
SEGMENT_FILENAME_PATTERN = "%Y%m%dT%H%M%SZ.mp4"
_SEGMENT_NAME_RE = re.compile(r"^(\d{8}T\d{6}Z)\.mp4$")
_SEGMENT_NAME_FORMAT = "%Y%m%dT%H%M%SZ"


class ProcessLike(Protocol):
    """subprocess.Popen 중 이 모듈이 쓰는 부분만 추린 것."""

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


ProcessRunner = Callable[[Sequence[str]], ProcessLike]


def _run(command: Sequence[str]) -> ProcessLike:
    return subprocess.Popen(command)


def parse_segment_recorded_at(path: Path) -> datetime | None:
    """세그먼트 파일 이름에서 녹화 시작 시각을 읽는다.

    파일 mtime을 쓰지 않는 이유는, mtime이 녹화 시작이 아니라 마지막 쓰기 시각이고
    파일을 옮기면 바뀌기 때문이다. 이름은 FFmpeg이 녹화 시작 시각으로 붙인 값이다.
    """
    match = _SEGMENT_NAME_RE.match(path.name)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(1), _SEGMENT_NAME_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


class Segmenter:
    """카메라 한 대의 RTSP를 일정 길이의 파일로 나눠 받는다."""

    def __init__(
        self,
        source: CameraSource,
        *,
        output_dir: Path,
        segment_seconds: int,
        process_runner: ProcessRunner = _run,
        sleep: Callable[[float], None] = time.sleep,
        startup_wait_seconds: float = 2.0,
    ) -> None:
        self._source = source
        self._output_dir = output_dir
        self._segment_seconds = segment_seconds
        self._process_runner = process_runner
        self._sleep = sleep
        self._startup_wait_seconds = startup_wait_seconds
        self._process: ProcessLike | None = None

    @property
    def camera_id(self) -> str:
        return self._source.camera_id

    @property
    def output_dir(self) -> Path:
        return self._output_dir

    def build_command(self) -> list[str]:
        """FFmpeg 명령을 만든다. 설정 확인과 테스트를 위해 분리해 둔다."""
        return [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "warning",
            # UDP는 패킷 손실로 영상이 깨진다. stream worker와 같은 이유로 TCP를 쓴다.
            "-rtsp_transport",
            "tcp",
            "-i",
            self._source.rtsp_url,
            # 재인코딩하지 않는다. CPU를 쓰지 않고 원본 화질을 유지한다.
            "-c",
            "copy",
            "-f",
            "segment",
            "-segment_time",
            str(self._segment_seconds),
            # 세그먼트 경계를 키프레임에 맞춘다. 없으면 앞부분이 재생되지 않는다.
            "-reset_timestamps",
            "1",
            "-strftime",
            "1",
            # 파일 이름에 UTC를 쓴다. 서버 시각대가 바뀌어도 순서가 유지된다.
            "-strftime_mkdir",
            "0",
            str(self._output_dir / SEGMENT_FILENAME_PATTERN),
        ]

    def start(self) -> None:
        if self._process is not None:
            return

        self._output_dir.mkdir(parents=True, exist_ok=True)
        try:
            process = self._process_runner(self.build_command())
        except OSError as error:
            raise SegmentationError(
                f"카메라 {self.camera_id}: FFmpeg을 실행하지 못했습니다 ({error})"
            ) from error

        self._process = process
        logger.info(
            "카메라 %s 세그먼트 녹화 시작 (%s)", self.camera_id, self._source.masked_url
        )

        self._sleep(self._startup_wait_seconds)
        exit_code = process.poll()
        if exit_code is not None:
            self._process = None
            raise SegmentationError(
                f"카메라 {self.camera_id}: FFmpeg이 시작 직후 종료했습니다 "
                f"(종료 코드 {exit_code}). RTSP 주소와 서버 기동 여부를 확인합니다."
            )

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def restart(self) -> None:
        logger.warning("카메라 %s 세그먼트 녹화를 재시작한다", self.camera_id)
        self.stop()
        self.start()

    def stop(self) -> None:
        process = self._process
        if process is None:
            return

        self._process = None
        # terminate로 보내면 FFmpeg이 마지막 세그먼트의 moov atom을 쓰고 끝낸다.
        # kill로 먼저 끊으면 그 파일이 재생 불가 상태로 남는다.
        process.terminate()
        try:
            process.wait(timeout=_TERMINATE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            logger.warning(
                "카메라 %s FFmpeg이 종료되지 않아 강제 종료한다. "
                "마지막 세그먼트가 손상될 수 있다.",
                self.camera_id,
            )
            process.kill()
            process.wait(timeout=_TERMINATE_TIMEOUT_SECONDS)
        logger.info("카메라 %s 세그먼트 녹화 종료", self.camera_id)
