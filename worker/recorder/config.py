"""recorder worker 설정. 환경변수에서 읽고 프로세스 시작 시 검증한다."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from shared.camera_sources import CameraSource, parse_stream_sources

_RECORDER_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = _RECORDER_DIR / "data"

__all__ = ["DEFAULT_DATA_DIR", "CameraSource", "RecorderSettings"]


class RecorderSettings(BaseSettings):
    """recorder worker가 쓰는 전체 설정."""

    model_config = SettingsConfigDict(env_file=_RECORDER_DIR / ".env", extra="ignore")

    app_env: Literal["local", "dev", "prod"]

    # stream worker와 같은 형식·같은 변수를 쓴다. 두 워커가 같은 MediaMTX를 본다.
    stream_sources: SecretStr

    # --- 세그먼트 녹화 ---
    # 세그먼트가 짧으면 객체 수가 늘고, 길면 유실 시 잃는 분량이 커진다.
    recording_segment_seconds: int = Field(default=600, ge=10, le=3600)
    recording_segment_dir: Path = DEFAULT_DATA_DIR / "segments"
    # FFmpeg이 죽어 마지막 세그먼트가 남는 경우를 감지하는 여유 시간.
    # 세그먼트 길이보다 짧으면 쓰는 중인 파일을 올리게 된다.
    recording_stale_after_seconds: float = Field(default=900.0, gt=0, le=7200)
    recording_upload_interval_seconds: float = Field(default=30.0, gt=0, le=3600)

    # --- 보존 기간 ---
    # 팀이 합의한 값이 아니다(결정 0004). 시작할 때 현재 값을 경고로 남긴다.
    recording_retention_days: int = Field(default=30, ge=1, le=3650)
    recording_retention_interval_seconds: float = Field(default=3600.0, gt=0, le=86400)

    # --- 객체 저장소 ---
    # local은 MinIO 없이 적재 경로를 확인하기 위한 개발용이다. 운영 보관 수단이 아니다.
    object_storage_backend: Literal["local", "minio"] = "local"
    object_storage_bucket: str = "office-recordings"
    object_storage_local_dir: Path = DEFAULT_DATA_DIR / "objects"

    # MinIO 접속 정보는 비밀값이라 기본값을 주지 않는다.
    object_storage_endpoint: str | None = None
    object_storage_access_key: SecretStr | None = None
    object_storage_secret_key: SecretStr | None = None
    object_storage_secure: bool = True

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @property
    def camera_sources(self) -> tuple[CameraSource, ...]:
        return parse_stream_sources(self.stream_sources.get_secret_value())

    # .env.example은 경로 항목을 비워 둔다. 빈 문자열이 그대로 오면 Path(".")가 되어
    # 실행 위치에 영상이 쌓인다. 비어 있으면 기본 경로를 쓰게 한다.
    @field_validator("recording_segment_dir", mode="before")
    @classmethod
    def _blank_segment_dir_uses_default(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return DEFAULT_DATA_DIR / "segments"
        return value

    @field_validator("object_storage_local_dir", mode="before")
    @classmethod
    def _blank_object_dir_uses_default(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return DEFAULT_DATA_DIR / "objects"
        return value

    @model_validator(mode="after")
    def _validate_environment_contract(self) -> Self:
        # 형식 오류를 요청 처리 중이 아니라 시작 시점에 드러낸다.
        self.camera_sources

        if self.recording_stale_after_seconds <= self.recording_segment_seconds:
            raise ValueError(
                "RECORDING_STALE_AFTER_SECONDS는 RECORDING_SEGMENT_SECONDS보다 커야 합니다. "
                "그러지 않으면 아직 쓰는 중인 세그먼트를 올리게 됩니다."
            )

        if self.object_storage_backend == "minio":
            missing_names = [
                name
                for name, value in (
                    ("OBJECT_STORAGE_ENDPOINT", self.object_storage_endpoint),
                    ("OBJECT_STORAGE_ACCESS_KEY", self.object_storage_access_key),
                    ("OBJECT_STORAGE_SECRET_KEY", self.object_storage_secret_key),
                )
                if value is None or not str(value).strip()
            ]
            if missing_names:
                raise ValueError(
                    "OBJECT_STORAGE_BACKEND=minio에 필요한 환경변수가 없습니다: "
                    + ", ".join(missing_names)
                )

        # 로컬 디렉터리는 운영 보관 수단이 아니다. 결정 0004가 기각한 방식이다.
        if self.app_env == "prod" and self.object_storage_backend == "local":
            raise ValueError(
                "APP_ENV=prod에서는 OBJECT_STORAGE_BACKEND=local을 쓸 수 없습니다. "
                "로컬 디렉터리는 개발용이며 보존 기간·접근 권한을 분리할 수 없습니다."
            )
        return self
