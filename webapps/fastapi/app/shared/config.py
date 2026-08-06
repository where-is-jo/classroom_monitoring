"""환경변수에서 읽는 애플리케이션 설정과 시작 시 검증."""

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
    mock_inputs_enabled: bool = False

    jwt_access_secret: SecretStr | None = None
    jwt_refresh_secret: SecretStr | None = None
    csrf_secret: SecretStr | None = None
    audit_ip_hash_secret: SecretStr | None = None
    web_origin: str | None = None
    auth_access_token_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    auth_refresh_token_ttl_seconds: int = Field(default=604800, ge=3600)
    auth_login_max_failures: int = Field(default=5, ge=1, le=20)
    auth_lockout_seconds: int = Field(default=900, ge=60, le=86400)
    auth_ip_max_failures: int = Field(default=20, ge=1, le=100)
    auth_ip_window_seconds: int = Field(default=300, ge=60, le=3600)
    auth_password_min_length: int = Field(default=12, ge=10, le=128)
    auth_seed_enabled: bool = False
    auth_seed_student_password: SecretStr | None = None
    auth_seed_staff_password: SecretStr | None = None
    auth_seed_admin_password: SecretStr | None = None
    auth_seed_system_operator_password: SecretStr | None = None

    employee_away_after_seconds: int = Field(default=180, ge=1, le=86400)
    employee_offsite_after_seconds: int = Field(default=3600, ge=1, le=604800)
    notification_mock_delivery_mode: Literal[
        "success", "fail_once", "always_fail"
    ] = "success"
    notification_mock_delivery_max_attempts: int = Field(default=3, ge=1, le=10)
    interview_wait_expires_after_hours: int = Field(default=24, ge=1, le=168)
    seat_occupancy_confidence_threshold: float = Field(default=0.6, ge=0, le=1)

    # 신뢰도 판정 임계값. 화면과 API가 아니라 서비스 계층에서 적용한다.
    high_confidence_threshold: float = Field(default=0.80, ge=0, le=1)
    medium_confidence_threshold: float = Field(default=0.50, ge=0, le=1)

    # 페이지네이션. 상한을 두지 않으면 전체 조회 요청이 들어온다.
    page_size_default: int = Field(default=50, ge=1)
    page_size_max: int = Field(default=200, ge=1, le=200)

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
            has_database_url = bool(
                self.database_url and self.database_url.get_secret_value().strip()
            )
            missing_names = [
                name
                for name, is_present in (
                    ("DATABASE_URL", has_database_url),
                    ("DATABASE_NAME", bool(self.database_name)),
                )
                if not is_present
            ]
            if missing_names:
                raise ValueError(
                    "MongoDB mode에 필요한 환경변수가 없습니다: "
                    + ", ".join(missing_names)
                )

        if self.app_env == "prod" and self.mock_inputs_enabled:
            raise ValueError("APP_ENV=prod에서는 MOCK_INPUTS_ENABLED를 활성화할 수 없습니다.")

        missing_security_names = [
            name
            for name, secret in (
                ("JWT_ACCESS_SECRET", self.jwt_access_secret),
                ("JWT_REFRESH_SECRET", self.jwt_refresh_secret),
                ("CSRF_SECRET", self.csrf_secret),
                ("AUDIT_IP_HASH_SECRET", self.audit_ip_hash_secret),
            )
            if secret is None or len(secret.get_secret_value()) < 32
        ]
        if not self.web_origin:
            missing_security_names.append("WEB_ORIGIN")
        if missing_security_names:
            raise ValueError(
                "인증에 필요한 환경변수가 없거나 너무 짧습니다: "
                + ", ".join(missing_security_names)
            )
        if not self.web_origin.startswith(("http://", "https://")):
            raise ValueError("WEB_ORIGIN은 http 또는 https origin이어야 합니다.")
        self.web_origin = self.web_origin.rstrip("/")

        if self.auth_seed_enabled:
            if self.app_env == "prod":
                raise ValueError("APP_ENV=prod에서는 AUTH_SEED_ENABLED를 활성화할 수 없습니다.")
            missing_seed_names = [
                name
                for name, secret in (
                    ("AUTH_SEED_STUDENT_PASSWORD", self.auth_seed_student_password),
                    ("AUTH_SEED_STAFF_PASSWORD", self.auth_seed_staff_password),
                    ("AUTH_SEED_ADMIN_PASSWORD", self.auth_seed_admin_password),
                    (
                        "AUTH_SEED_SYSTEM_OPERATOR_PASSWORD",
                        self.auth_seed_system_operator_password,
                    ),
                )
                if secret is None or not secret.get_secret_value()
            ]
            if missing_seed_names:
                raise ValueError(
                    "가상 사용자 seed에 필요한 환경변수가 없습니다: "
                    + ", ".join(missing_seed_names)
                )

        if self.medium_confidence_threshold > self.high_confidence_threshold:
            raise ValueError(
                "MEDIUM_CONFIDENCE_THRESHOLD는 HIGH_CONFIDENCE_THRESHOLD보다 클 수 없습니다."
            )
        if self.employee_away_after_seconds >= self.employee_offsite_after_seconds:
            raise ValueError(
                "EMPLOYEE_AWAY_AFTER_SECONDS는 EMPLOYEE_OFFSITE_AFTER_SECONDS보다 작아야 합니다."
            )
        if self.page_size_default > self.page_size_max:
            raise ValueError("PAGE_SIZE_DEFAULT는 PAGE_SIZE_MAX보다 클 수 없습니다.")
        return self
