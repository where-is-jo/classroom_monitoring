"""Minimal monitoring app settings and startup validation."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["local", "dev", "prod"]
    database_mode: Literal["memory", "mongodb"]
    database_url: SecretStr | None = None
    database_name: str | None = None
    database_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    demo_mode_enabled: bool = False
    seat_occupancy_confidence_threshold: float = Field(default=0.6, ge=0, le=1)
    page_size_default: int = Field(default=50, ge=1)
    page_size_max: int = Field(default=200, ge=1, le=200)

    # SSE settings
    sse_heartbeat_interval_seconds: int = Field(default=30, ge=1)
    sse_reconnection_timeout_seconds: int = Field(default=60, ge=1)

    # Detection event settings
    detection_event_max_detections_per_event: int = Field(default=100, ge=1)
    detection_event_stale_seconds: int = Field(default=300, ge=1)
    # --- 탐지 스냅샷 저장소 (결정 0011) ---
    # worker가 적재한 스냅샷을 읽기만 한다. fastapi는 만들지도 지우지도 않는다.
    # memory는 MinIO 없이 화면을 띄우기 위한 개발용이며 빈 목록을 돌려준다.
    snapshot_storage_backend: Literal["memory", "minio"] = "memory"
    snapshot_storage_bucket: str = "classroom-snapshots"
    snapshot_storage_endpoint: str | None = None
    snapshot_storage_access_key: SecretStr | None = None
    snapshot_storage_secret_key: SecretStr | None = None
    snapshot_storage_secure: bool = True
    snapshot_storage_timeout_seconds: float = Field(default=5.0, gt=0, le=60)

    @field_validator("database_name", mode="before")
    @classmethod
    def _empty_database_name_is_missing(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _validate_environment_contract(self) -> Self:
        if self.database_mode == "memory" and self.app_env != "local":
            raise ValueError("DATABASE_MODE=memory can only be used with APP_ENV=local.")
        if self.database_mode == "mongodb":
            has_url = bool(self.database_url and self.database_url.get_secret_value().strip())
            missing = [
                name
                for name, present in (
                    ("DATABASE_URL", has_url),
                    ("DATABASE_NAME", bool(self.database_name)),
                )
                if not present
            ]
            if missing:
                raise ValueError("Missing required environment variables for MongoDB mode: " + ", ".join(missing))
        if self.app_env == "prod" and self.demo_mode_enabled:
            raise ValueError("DEMO_MODE_ENABLED cannot be enabled with APP_ENV=prod.")
        if self.page_size_default > self.page_size_max:
            raise ValueError("PAGE_SIZE_DEFAULT must be less than or equal to PAGE_SIZE_MAX.")
            raise ValueError("PAGE_SIZE_DEFAULT는 PAGE_SIZE_MAX 이하여야 합니다.")
        if self.snapshot_storage_backend == "minio":
            missing_storage = [
                name
                for name, value in (
                    ("SNAPSHOT_STORAGE_ENDPOINT", self.snapshot_storage_endpoint),
                    ("SNAPSHOT_STORAGE_ACCESS_KEY", self.snapshot_storage_access_key),
                    ("SNAPSHOT_STORAGE_SECRET_KEY", self.snapshot_storage_secret_key),
                )
                if value is None or not str(value).strip()
            ]
            if missing_storage:
                raise ValueError(
                    "SNAPSHOT_STORAGE_BACKEND=minio에 필요한 환경변수가 없습니다: "
                    + ", ".join(missing_storage)
                )
        return self
