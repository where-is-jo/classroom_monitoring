"""Pydantic Settings의 환경 주입을 격리하는 테스트 helper."""

from __future__ import annotations

from typing import Any

from app.shared.config import Settings


def make_settings(**values: Any) -> Settings:
    """런타임 coercion과 의도적인 invalid 입력을 그대로 검증한다."""
    values.pop("_env_file", None)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]
