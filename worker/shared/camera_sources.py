"""영상 소스 목록의 공통 형식.

`stream`은 프레임을 읽으려고, `recorder`는 세그먼트를 저장하려고 같은 RTSP 소스에
붙는다. 형식을 워커마다 따로 파싱하면 같은 `STREAM_SOURCES` 값이 워커에 따라
다르게 해석될 수 있다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

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
    """연결할 영상 소스 하나. 소스별 식별자로 로그와 지표를 구분한다."""

    camera_id: str
    rtsp_url: str

    @property
    def masked_url(self) -> str:
        return mask_url_credentials(self.rtsp_url)


def parse_stream_sources(raw: str) -> tuple[CameraSource, ...]:
    """`camera-01=rtsp://...,camera-02=rtsp://...` 형식을 소스 목록으로 바꾼다.

    소스별 식별자를 URL과 함께 받는 이유는, 식별자가 로그·지표·저장 경로에서
    카메라를 구분하는 키가 되기 때문이다. URL을 키로 쓰면 자격 증명이 함께 노출된다.
    """
    sources: list[CameraSource] = []
    seen_ids: set[str] = set()

    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue

        camera_id, separator, rtsp_url = entry.partition("=")
        camera_id = camera_id.strip()
        rtsp_url = rtsp_url.strip()

        if not separator or not rtsp_url:
            raise ValueError(
                "STREAM_SOURCES 항목은 '<카메라 식별자>=<RTSP URL>' 형식이어야 합니다: "
                f"{camera_id or entry}"
            )
        if not _CAMERA_ID_PATTERN.match(camera_id):
            raise ValueError(
                "카메라 식별자는 소문자·숫자·하이픈만 쓸 수 있습니다: " f"{camera_id}"
            )
        if camera_id in seen_ids:
            raise ValueError(f"STREAM_SOURCES에 중복된 카메라 식별자가 있습니다: {camera_id}")
        if not rtsp_url.startswith("rtsp://"):
            # 값 자체는 자격 증명일 수 있으므로 식별자만 알린다.
            raise ValueError(f"카메라 {camera_id}의 URL이 rtsp:// 로 시작하지 않습니다.")

        seen_ids.add(camera_id)
        sources.append(CameraSource(camera_id=camera_id, rtsp_url=rtsp_url))

    if not sources:
        raise ValueError("STREAM_SOURCES에 영상 소스가 하나도 없습니다.")
    return tuple(sources)
