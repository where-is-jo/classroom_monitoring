"""recorder worker 설정. 환경변수·yml에서 읽고 프로세스 시작 시 검증한다.

값은 두 곳에서 온다.

- `.env.{APP_ENV}` — 환경마다 달라야 하는 값과 비밀값(`STREAM_SOURCES`,
  `OBJECT_STORAGE_BACKEND`와 MinIO 접속 정보). 커밋하지 않는다.
- `config/settings.yml` — 환경과 무관하게 같은 값. 커밋한다.

우선순위는 실제 OS 환경변수 > `.env.{APP_ENV}` > `config/settings.yml`이다
(`shared.settings_sources.customise_sources_with_yaml`).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict
from shared.camera_sources import CameraSource, parse_stream_sources
from shared.object_storage import ObjectStorageSettings
from shared.settings_sources import customise_sources_with_yaml

_RECORDER_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = _RECORDER_DIR / "data"

_APP_ENV_FOR_FILE_SELECTION = os.environ.get("APP_ENV", "local")

__all__ = ["DEFAULT_DATA_DIR", "CameraSource", "RecorderSettings"]


class RecorderSettings(ObjectStorageSettings):
    """recorder worker가 쓰는 전체 설정.

    객체 저장소 값(`OBJECT_STORAGE_*`)은 `ObjectStorageSettings`에서 온다.
    inference도 같은 mixin을 쓰므로 두 워커가 같은 변수를 같게 해석한다.
    `OBJECT_STORAGE_BACKEND`·`OBJECT_STORAGE_ENDPOINT`·`ACCESS_KEY`·`SECRET_KEY`는
    환경마다 실제로 달라지는 값이라 `.env.*`에 두고, 나머지는 `config/settings.yml`에 둔다.
    """

    model_config = SettingsConfigDict(
        env_file=_RECORDER_DIR / f".env.{_APP_ENV_FOR_FILE_SELECTION}",
        yaml_file=_RECORDER_DIR / "config" / "settings.yml",
        # PyYAML의 기본 파일 인코딩은 OS 로캘을 따른다. 한국어 Windows에서는 cp949라
        # yml의 한국어 주석을 읽다가 UnicodeDecodeError가 난다. 명시적으로 고정한다.
        yaml_file_encoding="utf-8",
        extra="ignore",
    )

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

    # shared는 기본값을 None으로 두고 조립 시점에 정하게 한다. recorder는 예전부터
    # 자기 data 디렉터리를 기본값으로 써 왔고, 그 동작을 바꾸지 않는다.
    object_storage_local_dir: Path = DEFAULT_DATA_DIR / "objects"

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @property
    def camera_sources(self) -> tuple[CameraSource, ...]:
        return parse_stream_sources(self.stream_sources.get_secret_value())

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return customise_sources_with_yaml(
            settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
        )

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

        self.validate_object_storage(app_env=self.app_env)
        return self
