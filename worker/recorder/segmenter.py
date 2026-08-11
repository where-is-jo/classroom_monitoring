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
from datetime import datetime
from pathlib import Path
from typing import IO, Protocol

from shared.camera_sources import CameraSource

from .errors import SegmentationError

logger = logging.getLogger(__name__)

_QUIT_TIMEOUT_SECONDS = 15.0
_TERMINATE_TIMEOUT_SECONDS = 10.0

# FFmpeg이 strftime으로 만드는 파일 이름. 업로드할 때 녹화 시각을 여기서 되읽는다.
#
# **이 이름은 로컬 시각이다.** FFmpeg의 -strftime은 localtime을 쓰며 UTC로 바꾸는
# 옵션이 없다. TZ=UTC 환경변수로 바뀌는 빌드도 있지만, 그건 서드파티 바이너리의
# C 런타임 동작에 기대는 것이라 다른 빌드에서 조용히 틀린다. 틀려도 파일은 정상으로
# 보이고 객체 키의 시각만 어긋나므로 알아채기 어렵다.
#
# 그래서 이름에 시각대를 표시하지 않고, 읽을 때 시스템 로컬 시각대를 붙여
# UTC로 변환한다. 변환은 build_object_key가 한다.
SEGMENT_FILENAME_PATTERN = "%Y%m%d_%H%M%S.mp4"
_SEGMENT_NAME_RE = re.compile(r"^(\d{8}_\d{6})\.mp4$")
_SEGMENT_NAME_FORMAT = "%Y%m%d_%H%M%S"


class ProcessLike(Protocol):
    """subprocess.Popen 중 이 모듈이 쓰는 부분만 추린 것."""

    stdin: IO[bytes] | None

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


ProcessRunner = Callable[[Sequence[str]], ProcessLike]


def _run(command: Sequence[str]) -> ProcessLike:
    # stdin을 파이프로 연다. 종료할 때 'q'를 보내 FFmpeg이 스스로 끝내게 하려는 것이다.
    # 파이프를 주므로 부모의 stdin을 건드리지 않는다.
    return subprocess.Popen(command, stdin=subprocess.PIPE)


def parse_segment_recorded_at(path: Path) -> datetime | None:
    """세그먼트 파일 이름에서 녹화 시작 시각을 읽는다. 결과는 시각대가 붙어 있다.

    파일 mtime을 쓰지 않는 이유는, mtime이 녹화 시작이 아니라 마지막 쓰기 시각이고
    파일을 옮기면 바뀌기 때문이다. 이름은 FFmpeg이 녹화를 시작할 때 붙인 값이다.

    이름은 로컬 시각이므로 시스템 시각대를 붙인다. 서머타임을 쓰는 지역에서는
    시계를 되돌리는 한 시간 동안 같은 이름이 두 번 나올 수 있고, 그때는 둘 중
    앞선 쪽으로 해석한다. 객체 키의 시각만 한 시간 어긋날 뿐 보존 기간 판정은
    저장소가 기록한 시각을 쓰므로 영향받지 않는다.
    """
    match = _SEGMENT_NAME_RE.match(path.name)
    if match is None:
        return None
    try:
        naive = datetime.strptime(match.group(1), _SEGMENT_NAME_FORMAT)
    except ValueError:
        return None
    # naive를 로컬 시각으로 보고 시각대를 붙인다.
    return naive.astimezone()


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
            # -nostdin을 쓰지 않는다. 종료할 때 stdin으로 'q'를 보내야 하기 때문이다.
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
            # 파일 이름을 시각으로 붙인다. 값은 로컬 시각이며 위 상수 주석을 따른다.
            "-strftime",
            "1",
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
        """FFmpeg이 쓰던 세그먼트를 완성하고 끝내게 한다.

        stdin에 'q'를 보낸다. FFmpeg이 문서로 정한 정상 종료 방법이며, 쓰던 파일의
        트레일러(moov atom)를 쓰고 끝낸다.

        terminate를 먼저 쓰지 않는 이유는 Windows에서 그것이 TerminateProcess라
        정리할 틈을 주지 않기 때문이다. 그러면 마지막 세그먼트가 moov 없이 남아
        재생할 수 없다. 실제로 확인한 차이이며, POSIX의 SIGTERM과 동작이 다르다.
        """
        process = self._process
        if process is None:
            return

        self._process = None
        if self._request_quit(process):
            logger.info("카메라 %s 세그먼트 녹화 종료", self.camera_id)
            return

        logger.warning(
            "카메라 %s FFmpeg이 종료 요청에 응답하지 않아 강제 종료한다. "
            "마지막 세그먼트가 손상될 수 있다.",
            self.camera_id,
        )
        process.terminate()
        try:
            process.wait(timeout=_TERMINATE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=_TERMINATE_TIMEOUT_SECONDS)
        logger.info("카메라 %s 세그먼트 녹화 종료", self.camera_id)

    def _request_quit(self, process: ProcessLike) -> bool:
        """'q'를 보내 정상 종료를 요청한다. 끝났으면 True."""
        stdin = process.stdin
        if stdin is None:
            return False

        try:
            stdin.write(b"q")
            stdin.flush()
            stdin.close()
        except (OSError, ValueError):
            # 프로세스가 이미 죽었거나 파이프가 닫혔다. 아래 강제 종료로 넘어간다.
            return False

        try:
            process.wait(timeout=_QUIT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            return False
        return True
