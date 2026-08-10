"""stream worker 설정. 환경변수에서 읽고 프로세스 시작 시 검증한다.

설정을 읽는 코드를 여기 한 곳에 모은다. 흩어지면 무엇이 필수인지 알 수 없게 된다.
값의 취급과 명명 규칙은 docs/conventions/environment-convention.md를 따른다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 저장 경로와 .env 위치를 실행 위치(CWD)가 아니라 이 파일 기준으로 잡는다.
# 상대 경로로 두면 저장소 루트에서 실행했을 때 worker 밖에 영상 디렉터리가 생기고,
# .gitignore 규칙에서도 벗어난다.
_STREAM_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = _STREAM_DIR / "data"

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


class StreamSettings(BaseSettings):
    """stream worker가 쓰는 전체 설정."""

    model_config = SettingsConfigDict(env_file=_STREAM_DIR / ".env", extra="ignore")

    app_env: Literal["local", "dev", "prod"]

    # 영상 소스 접속 정보는 비밀값 등급이라 기본값을 주지 않는다.
    stream_sources: SecretStr

    stream_reconnect_max_retry: int = Field(default=10, ge=1, le=100)
    stream_reconnect_delay_seconds: float = Field(default=1.0, gt=0, le=60)
    stream_startup_wait_seconds: float = Field(default=3.0, ge=0, le=60)
    # 연속으로 이만큼 프레임을 못 읽으면 끊긴 것으로 보고 재연결한다.
    # 1회 실패로 재연결하면 일시적인 패킷 손실마다 연결을 버리게 된다.
    stream_read_failure_tolerance: int = Field(default=30, ge=1, le=1000)

    # 모든 프레임을 추론에 보내지 않는다. 몇 프레임마다 1장을 고를지의 값이다.
    frame_sample_interval_frames: int = Field(default=20, ge=1, le=10000)

    # USB 카메라를 직접 RTSP로 올릴 때만 켠다. Jetson·CCTV가 직접 송출하면 필요 없다.
    rtsp_publish_enabled: bool = False
    # 입력 형식을 설정으로 둔 이유는 OS별 코드 분기를 만들지 않기 위해서다.
    # Windows는 dshow, Linux는 v4l2, macOS는 avfoundation을 쓴다.
    rtsp_publish_input_format: Literal["dshow", "v4l2", "avfoundation"] = "dshow"
    rtsp_publish_device_name: str | None = None
    rtsp_publish_target_url: SecretStr | None = None
    rtsp_publish_framerate: int = Field(default=20, ge=1, le=60)

    # 영상 저장 범위와 보존 기간이 합의되지 않아 기본값은 꺼둔다.
    # 결정 0004와 docs/architecture/README.md의 저장 책임 분리 절을 따른다.
    recording_enabled: bool = False
    recording_output_dir: Path = DEFAULT_DATA_DIR / "video"
    recording_fps: int = Field(default=20, ge=1, le=60)
    recording_segment_seconds: int = Field(default=3600, ge=60, le=86400)

    # 학습 데이터 확보용 프레임 저장. 운영 보관 수단이 아니라 개발용이라 기본은 꺼둔다.
    frame_capture_enabled: bool = False
    frame_capture_output_dir: Path = DEFAULT_DATA_DIR / "frames"

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @property
    def camera_sources(self) -> tuple[CameraSource, ...]:
        return parse_stream_sources(self.stream_sources.get_secret_value())

    # .env.example은 경로 항목을 비워 둔다. 빈 문자열이 그대로 오면 Path(".")가 되어
    # 실행 위치에 영상이 쌓인다. 비어 있으면 기본 경로를 쓰게 한다.
    @field_validator("recording_output_dir", mode="before")
    @classmethod
    def _blank_recording_dir_uses_default(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return DEFAULT_DATA_DIR / "video"
        return value

    @field_validator("frame_capture_output_dir", mode="before")
    @classmethod
    def _blank_capture_dir_uses_default(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return DEFAULT_DATA_DIR / "frames"
        return value

    @model_validator(mode="after")
    def _validate_environment_contract(self) -> Self:
        # 형식 오류를 요청 처리 중이 아니라 시작 시점에 드러낸다.
        self.camera_sources

        if self.rtsp_publish_enabled:
            missing_names = [
                name
                for name, value in (
                    ("RTSP_PUBLISH_DEVICE_NAME", self.rtsp_publish_device_name),
                    ("RTSP_PUBLISH_TARGET_URL", self.rtsp_publish_target_url),
                )
                if value is None or not str(value).strip()
            ]
            if missing_names:
                raise ValueError(
                    "RTSP_PUBLISH_ENABLED=true에 필요한 환경변수가 없습니다: "
                    + ", ".join(missing_names)
                )

        # 영상에는 사무실 구성원의 얼굴이 담긴다. 보존 기간·접근 권한이 정해지기 전까지
        # 운영 환경에서 상시 저장을 켤 수 없게 막는다.
        if self.app_env == "prod":
            enabled_storage_names = [
                name
                for name, is_enabled in (
                    ("RECORDING_ENABLED", self.recording_enabled),
                    ("FRAME_CAPTURE_ENABLED", self.frame_capture_enabled),
                )
                if is_enabled
            ]
            if enabled_storage_names:
                raise ValueError(
                    "영상 저장 범위와 보존 기간이 합의되기 전까지 APP_ENV=prod에서는 "
                    "다음을 활성화할 수 없습니다: " + ", ".join(enabled_storage_names)
                )
        return self
