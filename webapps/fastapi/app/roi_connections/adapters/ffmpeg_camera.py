"""RTSP 카메라에서 현재 프레임 한 장을 ffmpeg로 받아 오는 어댑터.

**왜 fastapi가 카메라에 직접 붙는가**는 결정 0031에 있다. 요약하면 ROI를 그릴 기준
화면이 필요한데, 그 화면을 만들어 줄 수 있는 곳이 지금 구성에서 여기뿐이다.

**왜 ffmpeg 프로세스인가**: CCTV가 HEVC(H.265)를 내보내는데 순수 파이썬 디코더로는
읽을 수 없다. worker/recorder도 같은 이유로 ffmpeg를 쓰고 있어 팀에 이미 있는 수단이다.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from ..errors import CameraFrameUnavailableError

logger = logging.getLogger(__name__)

# worker/shared/camera_sources.py와 같은 규칙이다. 카메라 식별자는 로그·지표·저장
# 경로에서 카메라를 구분하는 키라서 두 서비스가 같은 이름을 써야 한다.
_CAMERA_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def mask_url_credentials(url: str) -> str:
    """RTSP URL에서 자격 증명을 지운 로그용 문자열을 만든다.

    카메라 접속 정보는 비밀값이므로 로그·오류 메시지에 원본을 남기지 않는다.
    """
    try:
        split = urlsplit(url)
    except ValueError:
        return "<파싱할 수 없는 URL>"
    if not split.hostname:
        return "<host 없는 URL>"
    netloc = split.hostname
    if split.port is not None:
        netloc = f"{netloc}:{split.port}"
    if split.username or split.password:
        netloc = f"***@{netloc}"
    return urlunsplit((split.scheme, netloc, split.path, "", ""))


@dataclass(frozen=True)
class CameraSource:
    """캡처할 카메라 하나. 식별자와 접속 URL을 함께 갖는다."""

    camera_id: str
    rtsp_url: str

    @property
    def masked_url(self) -> str:
        return mask_url_credentials(self.rtsp_url)


def parse_camera_sources(raw: str) -> dict[str, CameraSource]:
    """`camera-01=rtsp://...,camera-02=rtsp://...`를 카메라 식별자 사전으로 바꾼다.

    worker의 `STREAM_SOURCES`와 같은 형식이다. 형식을 서비스마다 따로 정하면 같은
    값이 서비스에 따라 다르게 해석된다.
    """
    sources: dict[str, CameraSource] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        camera_id, separator, rtsp_url = entry.partition("=")
        camera_id = camera_id.strip()
        rtsp_url = rtsp_url.strip()
        if not separator or not rtsp_url:
            raise ValueError(
                "CAMERA_RTSP_SOURCES 항목은 '<카메라 식별자>=<RTSP URL>' 형식이어야 "
                f"합니다: {camera_id or entry}"
            )
        if not _CAMERA_ID_PATTERN.match(camera_id):
            raise ValueError(f"카메라 식별자는 소문자·숫자·하이픈만 쓸 수 있습니다: {camera_id}")
        if camera_id in sources:
            raise ValueError(f"CAMERA_RTSP_SOURCES에 중복된 카메라 식별자가 있습니다: {camera_id}")
        if not rtsp_url.startswith("rtsp://"):
            # 값 자체가 자격 증명일 수 있으므로 식별자만 알린다.
            raise ValueError(f"카메라 {camera_id}의 URL이 rtsp:// 로 시작하지 않습니다.")
        sources[camera_id] = CameraSource(camera_id=camera_id, rtsp_url=rtsp_url)
    return sources


class FfmpegCameraFrameGrabber:
    """ffmpeg를 한 번 실행해 프레임 한 장을 JPEG로 받는다.

    스트림을 계속 붙들지 않고 캡처할 때마다 새로 연결한다. ROI 등록은 가끔 누르는
    버튼이라 상시 연결을 유지할 이유가 없고, 유지하면 카메라 세션 수만 늘어난다.
    """

    def __init__(
        self,
        sources: dict[str, CameraSource],
        *,
        timeout_seconds: float,
        ffmpeg_path: str = "ffmpeg",
    ) -> None:
        self._sources = sources
        self._timeout_seconds = timeout_seconds
        self._ffmpeg_path = ffmpeg_path

    def is_available(self, camera_id: str) -> bool:
        return camera_id in self._sources

    def capture_jpeg(self, camera_id: str) -> bytes:
        source = self._sources.get(camera_id)
        if source is None:
            raise CameraFrameUnavailableError(
                "이 카메라의 접속 정보가 설정되어 있지 않아 화면을 가져올 수 없습니다."
            )
        # URL은 인자 배열로 넘기고 shell을 거치지 않는다. shell=True로 두면 URL 안의
        # 문자가 명령으로 해석될 수 있다.
        command = [
            self._ffmpeg_path,
            "-y",
            "-v",
            "error",
            # UDP는 방화벽·NAT를 넘지 못하는 경우가 많고, 넘더라도 손실된 패킷이 그대로
            # 깨진 프레임이 된다. 한 장을 확실히 받는 쪽이 중요하다.
            "-rtsp_transport",
            "tcp",
            # ffmpeg의 timeout은 마이크로초다. 프로세스 전체 상한(subprocess timeout)과
            # 별개로, 응답 없는 카메라에서 ffmpeg 자신이 먼저 빠져나오게 한다.
            "-timeout",
            str(int(self._timeout_seconds * 1_000_000)),
            "-i",
            source.rtsp_url,
            "-frames:v",
            "1",
            "-q:v",
            "3",
            "-f",
            "image2",
            # stdout으로 받는다. 임시 파일을 쓰면 캡처한 강의실 화면이 디스크에 남는다.
            "-",
        ]
        try:
            # 인자 배열로 넘기고 shell을 쓰지 않는다. URL은 명령으로 해석되지 않는다.
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=self._timeout_seconds,
                check=False,
            )
        except FileNotFoundError as error:
            logger.error("ffmpeg 실행 파일을 찾을 수 없다: %s", self._ffmpeg_path)
            raise CameraFrameUnavailableError(
                "영상 캡처 도구(ffmpeg)를 찾을 수 없어 화면을 가져오지 못했습니다."
            ) from error
        except subprocess.TimeoutExpired as error:
            logger.warning("카메라 %s 캡처 시간 초과: %s", camera_id, source.masked_url)
            raise CameraFrameUnavailableError(
                "카메라가 제한 시간 안에 응답하지 않아 화면을 가져오지 못했습니다."
            ) from error

        if completed.returncode != 0 or not completed.stdout:
            # ffmpeg의 stderr에는 URL이 통째로 찍힐 수 있어 사용자 응답에 넣지 않는다.
            logger.warning(
                "카메라 %s 캡처 실패 (exit=%s): %s",
                camera_id,
                completed.returncode,
                source.masked_url,
            )
            raise CameraFrameUnavailableError(
                "카메라에서 현재 화면을 가져오지 못했습니다. 연결 상태를 확인해 주세요."
            )
        return completed.stdout


class UnavailableCameraFrameGrabber:
    """접속 정보가 설정되지 않았을 때 쓰는 대역.

    캡처만 막고 ROI 화면의 나머지 기능은 그대로 두기 위해 예외 대신 이 구현을 끼운다.
    """

    def is_available(self, camera_id: str) -> bool:
        return False

    def capture_jpeg(self, camera_id: str) -> bytes:
        raise CameraFrameUnavailableError(
            "카메라 접속 정보(CAMERA_RTSP_SOURCES)가 설정되지 않아 화면을 가져올 수 없습니다."
        )
