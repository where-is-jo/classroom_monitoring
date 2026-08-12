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
        return self
