"""Minimal monitoring app settings and startup validation.

값은 두 곳에서 온다.

- ``.env.{APP_ENV}`` — 환경마다 달라야 하는 값과 비밀값(``APP_ENV``, ``DATABASE_MODE``,
  ``DATABASE_URL``, ``DATABASE_NAME``, ``FACE_ANALYZER_MODE``, ``FACE_ANALYZER_URL``,
  ``SNAPSHOT_STORAGE_BACKEND``와 MinIO 접속 정보). 커밋하지 않는다.
- ``config/settings.yml`` — 환경과 무관하게 같은 값(타임아웃, 판정 임계값, quota 등).
  커밋한다.

우선순위는 실제 OS 환경변수 > ``.env.{APP_ENV}`` 파일 > ``config/settings.yml``이다.
규칙은 ``docs/conventions/environment-convention.md``를 따른다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

# .env.*와 config/를 실행 위치(CWD)가 아니라 이 파일 기준으로 잡는다.
# config.py는 app/shared/에 있으므로 두 단계 위가 webapps/fastapi다.
_FASTAPI_DIR = Path(__file__).resolve().parent.parent.parent

# 실제 OS 환경변수로 어떤 .env.{APP_ENV} 파일을 읽을지 정한다. 없으면 local로 본다 —
# 손이 덜 가는 local을 기본값으로 두는 기존 원칙과 같다.
_APP_ENV_FOR_FILE_SELECTION = os.environ.get("APP_ENV", "local")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_FASTAPI_DIR / f".env.{_APP_ENV_FOR_FILE_SELECTION}",
        yaml_file=_FASTAPI_DIR / "config" / "settings.yml",
        # PyYAML의 기본 파일 인코딩은 OS 로캘을 따른다. 한국어 Windows에서는 cp949라
        # yml의 한국어 주석을 읽다가 UnicodeDecodeError가 난다. 명시적으로 고정한다.
        yaml_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["local", "dev", "prod"] = "local"
    database_mode: Literal["memory", "mongodb"] = "memory"
    database_url: SecretStr | None = None
    database_name: str | None = None
    database_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    demo_mode_enabled: bool = False
    seat_occupancy_confidence_threshold: float = Field(default=0.6, ge=0, le=1)
    page_size_default: int = Field(default=50, ge=1)
    page_size_max: int = Field(default=200, ge=1, le=200)
    # 오프라인 migration cutover 게이트. 승인된 암호화 target/KMS가 준비된
    # 경우에만 true로 전환한다 — false면 migration run을 차단한다.
    migration_encryption_target_approved: bool = False
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
    # 분석 companion 프로세스의 실행 방식과 주소 — local(synthetic)과 dev/prod(http)에서
    # 실제로 다른 값을 쓴다.
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

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # 실제 OS 환경변수 > .env.{APP_ENV} 파일 > config/settings.yml 순으로 읽는다.
        # yml에 있는 값도 필요하면 .env.*나 실제 export로 즉석 재정의할 수 있다.
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )

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
