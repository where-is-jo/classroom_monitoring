"""PyMongo client·database 선택과 공통 상태 확인.

MongoDB 접속 정보는 이 모듈 밖으로 노출하지 않는다. 기능 어댑터는 선택된
``Database``만 받고, client 조립은 ``shared/dependencies.py``에서 수행한다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from pydantic import SecretStr
from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import ConfigurationError, PyMongoError

type MongoDocument = dict[str, Any]
type MongoDatabase = Database[MongoDocument]
type IndexInitializer = Callable[[MongoDatabase], None]


class DatabaseOperationError(RuntimeError):
    """접속 문자열이나 내부 주소를 포함하지 않는 MongoDB 경계 오류."""


def create_mongo_client(
    database_url: SecretStr,
    *,
    timeout_seconds: float,
) -> MongoClient[MongoDocument]:
    """동기 service/repository와 함께 사용할 제한 시간 적용 PyMongo client를 만든다."""
    timeout_milliseconds = max(1, int(timeout_seconds * 1000))
    try:
        return MongoClient(
            database_url.get_secret_value(),
            connectTimeoutMS=timeout_milliseconds,
            serverSelectionTimeoutMS=timeout_milliseconds,
            tz_aware=True,
        )
    except (ConfigurationError, ValueError):
        raise DatabaseOperationError("DATABASE_URL 형식이 올바르지 않습니다.") from None


def select_database(
    client: MongoClient[MongoDocument],
    database_name: str,
) -> MongoDatabase:
    """명시적으로 검증된 이름의 database를 선택한다."""
    try:
        return client[database_name]
    except (ConfigurationError, ValueError):
        raise DatabaseOperationError("DATABASE_NAME 형식이 올바르지 않습니다.") from None


def ping_database(database: MongoDatabase) -> None:
    """MongoDB가 요청을 처리할 수 있는지 확인한다."""
    try:
        database.command("ping")
    except PyMongoError:
        raise DatabaseOperationError("MongoDB를 사용할 수 없습니다.") from None


def initialize_indexes(
    database: MongoDatabase,
    initializers: Iterable[IndexInitializer],
) -> None:
    """기능별 idempotent index initializer를 한 진입점에서 실행한다."""
    try:
        for initialize in initializers:
            initialize(database)
    except PyMongoError:
        raise DatabaseOperationError("MongoDB index 초기화에 실패했습니다.") from None


def validate_test_database_name(database_name: str) -> None:
    """통합 테스트가 개발·운영 database를 향하지 않도록 이름을 제한한다."""
    if not database_name.startswith("test_"):
        raise ValueError("MongoDB 통합 테스트 database 이름은 'test_'로 시작해야 합니다.")
