"""환경별 필수 설정과 안전 제약 테스트."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.shared.config import Settings


def test_memory_mode는_local에서_명시적으로_선택할_수_있다() -> None:
    settings = Settings(
        _env_file=None,
        app_env="local",
        database_mode="memory",
    )

    assert settings.database_url is None
    assert settings.database_name is None


def test_인증_비밀값과_WEB_ORIGIN은_기본값_없이_필수다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "JWT_ACCESS_SECRET",
        "JWT_REFRESH_SECRET",
        "CSRF_SECRET",
        "AUDIT_IP_HASH_SECRET",
        "WEB_ORIGIN",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValidationError) as raised:
        Settings(_env_file=None, app_env="local", database_mode="memory")

    message = str(raised.value)
    assert "JWT_ACCESS_SECRET" in message
    assert "WEB_ORIGIN" in message


def test_database_mode가_없으면_시작_설정_검증이_실패한다() -> None:
    with pytest.raises(ValidationError) as raised:
        Settings(_env_file=None, app_env="local", database_mode=None)

    assert "database_mode" in str(raised.value)


def test_mongodb_mode는_URL과_database_이름이_필수다() -> None:
    with pytest.raises(ValidationError) as raised:
        Settings(_env_file=None, app_env="dev", database_mode="mongodb")

    message = str(raised.value)
    assert "DATABASE_URL" in message
    assert "DATABASE_NAME" in message


def test_memory_mode는_dev와_prod에서_거부된다() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="dev", database_mode="memory")


def test_prod에서는_mock_입력을_활성화할_수_없다() -> None:
    with pytest.raises(ValidationError) as raised:
        Settings(
            _env_file=None,
            app_env="prod",
            database_mode="mongodb",
            database_url="mongodb://example.invalid",
            database_name="smart_office",
            mock_inputs_enabled=True,
        )

    assert "MOCK_INPUTS_ENABLED" in str(raised.value)


def test_prod에서는_가상_사용자_seed를_활성화할_수_없다() -> None:
    with pytest.raises(ValidationError) as raised:
        Settings(
            _env_file=None,
            app_env="prod",
            database_mode="mongodb",
            database_url="mongodb://example.invalid",
            database_name="smart_office",
            auth_seed_enabled=True,
            auth_seed_student_password="StudentPassword1!",
            auth_seed_staff_password="StaffPassword12!",
            auth_seed_admin_password="AdminPassword12!",
            auth_seed_system_operator_password="OperatorPassword1!",
        )

    assert "AUTH_SEED_ENABLED" in str(raised.value)


def test_페이지네이션_기본값은_최댓값을_넘을_수_없다() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env="local",
            database_mode="memory",
            page_size_default=200,
            page_size_max=100,
        )


def test_직원_AWAY_기준은_OFFSITE_기준보다_작아야_한다() -> None:
    with pytest.raises(ValidationError) as raised:
        Settings(
            _env_file=None,
            app_env="local",
            database_mode="memory",
            employee_away_after_seconds=3600,
            employee_offsite_after_seconds=3600,
        )

    assert "EMPLOYEE_AWAY_AFTER_SECONDS" in str(raised.value)


def test_mock_delivery_mode와_최대시도는_제한된_설정만_허용한다() -> None:
    settings = Settings(
        _env_file=None,
        app_env="local",
        database_mode="memory",
        notification_mock_delivery_mode="always_fail",
        notification_mock_delivery_max_attempts=1,
    )
    assert settings.notification_mock_delivery_mode == "always_fail"
    assert settings.notification_mock_delivery_max_attempts == 1

    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env="local",
            database_mode="memory",
            notification_mock_delivery_mode="network",  # type: ignore[arg-type]
        )


def test_interview_wait_expiration_hours_has_safe_bounds() -> None:
    settings = Settings(
        _env_file=None,
        app_env="local",
        database_mode="memory",
        interview_wait_expires_after_hours=24,
    )
    assert settings.interview_wait_expires_after_hours == 24

    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env="local",
            database_mode="memory",
            interview_wait_expires_after_hours=0,
        )

    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env="local",
            database_mode="memory",
            interview_wait_expires_after_hours=169,
        )


def test_seat_occupancy_confidence_threshold_is_bounded() -> None:
    assert Settings(
        _env_file=None,
        app_env="local",
        database_mode="memory",
        seat_occupancy_confidence_threshold=0.6,
    ).seat_occupancy_confidence_threshold == 0.6

    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env="local",
            database_mode="memory",
            seat_occupancy_confidence_threshold=1.01,
        )
