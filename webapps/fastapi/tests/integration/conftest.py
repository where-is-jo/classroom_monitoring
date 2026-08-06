"""명시적으로 제공된 안전한 MongoDB 통합 테스트 database fixture."""

from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any

import pytest
from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import ConfigurationError

from app.shared.database import validate_test_database_name


@pytest.fixture(scope="module")
def mongodb_database() -> Generator[Database[dict[str, Any]], None, None]:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL이 없어 MongoDB 통합 테스트를 건너뜁니다.")

    client = MongoClient(database_url, serverSelectionTimeoutMS=5000, tz_aware=True)
    try:
        try:
            database = client.get_default_database()
        except ConfigurationError:
            pytest.fail("TEST_DATABASE_URL 경로에 test_ 접두사의 database 이름이 필요합니다.")
        validate_test_database_name(database.name)
        database.command("ping")
        yield database
    finally:
        client.close()
