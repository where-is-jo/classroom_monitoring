"""Minimal monitoring app settings and startup validation."""

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

    # --- 자연어 탐지 검색 ---
    # stub은 LLM 없이 계약과 화면을 확인하기 위한 대역이며 "오늘 하루"만 돌려준다.
    # 기본을 stub으로 두는 이유는 개발과 테스트가 GPU 서버에 매이지 않게 하기 위해서다.
    llm_search_mode: Literal["stub", "llama"] = "stub"
    llm_search_url: str = "http://127.0.0.1:8008"
    # 생성은 조회보다 훨씬 느려서 다른 외부 호출(5초)과 같은 값을 쓸 수 없다.
    llm_search_timeout_seconds: float = Field(default=20, gt=0, le=120)
    # llama-server의 LLAMA_ARG_ALIAS와 같은 값이어야 한다.
    llm_search_model: str = "gemma"
    # 기간 상한을 넘는 질문은 거절하지 않고 이 길이로 줄인 뒤 사용자에게 알린다.
    llm_search_max_span_days: int = Field(default=7, ge=1, le=31)
    # 탐지 인원 변화를 판정하려면 원본 이벤트를 봐야 한다. 카메라 한 대에서 한 번에
    # 읽어 오는 이벤트 수의 상한이며, 걸리면 결과에 truncated로 표시한다.
    llm_search_scan_limit: int = Field(default=500, ge=1, le=5000)

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
        if self.llm_search_mode == "llama" and not self.llm_search_url.strip():
            raise ValueError("LLM_SEARCH_MODE=llama에는 LLM_SEARCH_URL이 필요합니다.")
        return self
