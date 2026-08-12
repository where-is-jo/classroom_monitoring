"""최소 모니터링 앱 설정과 시작 시 검증."""

from __future__ import annotations

from pathlib import Path
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
    face_enrollment_required_samples: int = Field(default=300, ge=1, le=2000)
    face_pose_front_quota: int = Field(default=60, ge=1)
    face_pose_left_quota: int = Field(default=60, ge=1)
    face_pose_right_quota: int = Field(default=60, ge=1)
    face_pose_up_quota: int = Field(default=60, ge=1)
    face_pose_down_quota: int = Field(default=60, ge=1)
    face_detection_confidence_min: float = Field(default=0.65, ge=0, le=1)
    face_size_ratio_min: float = Field(default=0.08, ge=0, le=1)
    face_roll_degrees_max: float = Field(default=20, gt=0, le=90)
    face_blur_score_min: float = Field(default=0.3, ge=0, le=1)
    face_brightness_score_min: float = Field(default=0.2, ge=0, le=1)
    face_landmark_confidence_min: float = Field(default=0.8, ge=0, le=1)
    face_occlusion_score_max: float = Field(default=0.3, ge=0, le=1)
    face_duplicate_score_max: float = Field(default=0.99, ge=0, le=1)
    face_motion_speed_dps_max: float = Field(default=220, gt=0, le=1000)
    face_yaw_side_degrees: float = Field(default=10, gt=0, le=90)
    face_pitch_side_degrees: float = Field(default=8, gt=0, le=90)
    face_local_sample_storage_enabled: bool = False
    face_local_sample_storage_dir: Path = Path("local_face_data")
    face_analyzer_mode: Literal["synthetic", "http"] = "synthetic"
    face_analyzer_url: str = "http://127.0.0.1:8100"
    face_analyzer_timeout_seconds: float = Field(default=5, gt=0, le=30)

    @field_validator("database_name", mode="before")
    @classmethod
    def _empty_database_name_is_missing(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _validate_environment_contract(self) -> Self:
        if self.database_mode == "memory" and self.app_env != "local":
            raise ValueError("DATABASE_MODE=memory는 APP_ENV=local에서만 사용할 수 있습니다.")
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
                raise ValueError("MongoDB mode에 필요한 환경변수가 없습니다: " + ", ".join(missing))
        if self.app_env == "prod" and self.demo_mode_enabled:
            raise ValueError("APP_ENV=prod에서는 DEMO_MODE_ENABLED를 활성화할 수 없습니다.")
        if self.app_env != "local" and self.face_local_sample_storage_enabled:
            raise ValueError("얼굴 샘플 로컬 저장은 APP_ENV=local에서만 사용할 수 있습니다.")
        if self.page_size_default > self.page_size_max:
            raise ValueError("PAGE_SIZE_DEFAULT는 PAGE_SIZE_MAX 이하여야 합니다.")
        quota_total = sum(
            (
                self.face_pose_front_quota,
                self.face_pose_left_quota,
                self.face_pose_right_quota,
                self.face_pose_up_quota,
                self.face_pose_down_quota,
            )
        )
        if quota_total != self.face_enrollment_required_samples:
            raise ValueError("Pose quota 합계는 필수 샘플 수와 같아야 합니다.")
        return self
