"""Minimal monitoring app settings and startup validation."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlparse

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
    face_enrollment_required_samples: int = Field(default=120, ge=1, le=2000)
    face_enrollment_augmented_samples: int = Field(default=180, ge=0, le=10000)
    face_pose_front_quota: int = Field(default=32, ge=1)
    face_pose_left_quota: int = Field(default=24, ge=1)
    face_pose_right_quota: int = Field(default=24, ge=1)
    face_pose_up_quota: int = Field(default=20, ge=1)
    face_pose_down_quota: int = Field(default=20, ge=1)
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

    # SSE settings
    sse_heartbeat_interval_seconds: int = Field(default=30, ge=1)
    sse_reconnection_timeout_seconds: int = Field(default=60, ge=1)

    # --- WHEP 재생 proxy와 재생 세션 (결정 0014) ---
    # proxy target은 이 base URL과 source의 camera_id로만 조립한다(SSRF 차단).
    # 경로 접두사(예: /webrtc)가 필요하면 base URL에 함께 넣는다.
    whep_base_url: str = "http://127.0.0.1:8889"
    whep_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    playback_session_ttl_seconds: int = Field(default=300, ge=30, le=3600)
    # local/http 개발 환경에서는 false로 내려야 cookie가 전송된다(ADR 남은 일).
    playback_session_cookie_secure: bool = True
    playback_session_sdp_max_bytes: int = Field(default=65536, ge=1024, le=1048576)

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

    @field_validator("whep_base_url")
    @classmethod
    def _whep_base_url_must_be_http(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("WHEP_BASE_URL must be an http(s) URL.")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("WHEP_BASE_URL must not contain credentials.")
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
                raise ValueError(
                    "Missing required environment variables for MongoDB mode: " + ", ".join(missing)
                )
        if self.app_env == "prod" and self.demo_mode_enabled:
            raise ValueError("APP_ENV=prod에서는 DEMO_MODE_ENABLED를 활성화할 수 없습니다.")
        if self.app_env != "local" and self.face_local_sample_storage_enabled:
            raise ValueError("얼굴 샘플 로컬 저장은 APP_ENV=local에서만 사용할 수 있습니다.")
        if self.page_size_default > self.page_size_max:
            raise ValueError("PAGE_SIZE_DEFAULT must be less than or equal to PAGE_SIZE_MAX.")
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
