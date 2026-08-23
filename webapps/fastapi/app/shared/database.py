"""PyMongo client·database 선택과 공통 상태 확인.

MongoDB 접속 정보는 이 모듈 밖으로 노출하지 않는다. 기능 어댑터는 선택된
``Database``만 받고, client 조립은 ``shared/dependencies.py``에서 수행한다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from bson import ObjectId
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


def document_id(document: MongoDocument) -> str:
    """문서의 ``_id``를 문자열로 읽는다. ``ObjectId``도 받아들인다.

    앱이 만드는 문서의 ``_id``는 항상 문자열(UUID)이다. 그런데 Compass·mongosh·
    적재 스크립트처럼 앱 밖에서 넣은 문서는 ``_id``를 생략하기 쉽고, 그러면 MongoDB가
    ``ObjectId``를 자동으로 부여한다. 그 문서를 읽을 때 문자열만 받아들이면 목록
    조회가 통째로 실패한다 — 실제로 학생 3건 때문에 학생·좌석·ROI 화면이 모두
    500이 났다.

    **읽기만 관대하게 하고 쓰기는 그대로 문자열로 둔다.** 앱이 새로 만드는 문서까지
    ``ObjectId``로 바꾸면 두 형식이 영구히 섞인다. 이미 들어온 데이터를 살리는 것과
    새 데이터의 형식을 정하는 것은 다른 문제다.
    """
    value = document["_id"]
    if isinstance(value, str):
        if not value:
            raise ValueError("빈 _id는 사용할 수 없습니다.")
        return value
    if isinstance(value, ObjectId):
        return str(value)
    raise TypeError("_id는 문자열이거나 ObjectId여야 합니다.")


def document_id_filter(value: str) -> MongoDocument:
    """``_id``로 문서를 찾는 filter를 만든다. 두 형식을 모두 맞춘다.

    `document_id`가 ``ObjectId``를 문자열로 돌려주므로, 그 값을 그대로 다시 조회하면
    ``{"_id": "6a89..."}``가 되어 원본 ``ObjectId`` 문서에 걸리지 않는다. 목록은
    보이는데 상세·수정만 조용히 실패하는 상태가 되므로 조회 쪽도 함께 맞춰야 한다.
    """
    if ObjectId.is_valid(value):
        return {"_id": {"$in": [value, ObjectId(value)]}}
    return {"_id": value}


def validate_test_database_name(database_name: str) -> None:
    """통합 테스트가 개발·운영 database를 향하지 않도록 이름을 제한한다."""
    if not database_name.startswith("test_"):
        raise ValueError("MongoDB 통합 테스트 database 이름은 'test_'로 시작해야 합니다.")
