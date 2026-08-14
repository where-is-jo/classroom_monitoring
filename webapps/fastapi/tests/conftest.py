"""최소 모니터링 테스트의 공통 실행 환경."""

from __future__ import annotations

import os

import pytest

os.environ["APP_ENV"] = "local"
os.environ["DATABASE_MODE"] = "memory"
os.environ["DATABASE_URL"] = ""
os.environ["DATABASE_NAME"] = ""
os.environ["DEMO_MODE_ENABLED"] = "false"
os.environ["FACE_ANALYZER_MODE"] = "synthetic"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "mongodb: TEST_DATABASE_URL이 있을 때만 실행하는 MongoDB 통합 테스트",
    )
