"""외부 MongoDB 없이 공통 database 경계를 검증한다."""

from __future__ import annotations

import pytest
from pydantic import SecretStr
from pymongo.errors import ServerSelectionTimeoutError

from app.events.adapters.mongo_repository import MongoEventRepository
from app.shared.database import (
    DatabaseOperationError,
    create_mongo_client,
    initialize_indexes,
    ping_database,
    validate_test_database_name,
)


class FakeIndexCollection:
    def __init__(self) -> None:
        self.indexes: dict[str, tuple[tuple[str, int], ...]] = {}

    def create_index(self, keys, *, name: str) -> str:
        specification = tuple(keys)
        existing = self.indexes.get(name)
        if existing is not None and existing != specification:
            raise AssertionError("같은 이름의 index 정의가 달라졌습니다.")
        self.indexes[name] = specification
        return name


class FakeIndexDatabase:
    def __init__(self) -> None:
        self.collection = FakeIndexCollection()

    def __getitem__(self, collection_name: str) -> FakeIndexCollection:
        assert collection_name == "events"
        return self.collection


class FailingPingDatabase:
    def command(self, command_name: str) -> None:
        assert command_name == "ping"
        raise ServerSelectionTimeoutError(
            "credential-marker@internal-mongodb.invalid connection failed"
        )


def test_index_초기화는_두_번_호출해도_하나의_정의만_유지한다() -> None:
    database = FakeIndexDatabase()

    initialize_indexes(database, [MongoEventRepository.ensure_indexes])
    initialize_indexes(database, [MongoEventRepository.ensure_indexes])

    assert database.collection.indexes == {
        "events_detected_at_desc": (("detected_at", -1),)
    }


def test_ping_실패_예외에는_내부_주소와_자격_정보가_남지_않는다() -> None:
    with pytest.raises(DatabaseOperationError) as raised:
        ping_database(FailingPingDatabase())

    message = str(raised.value)
    assert "credential-marker" not in message
    assert "internal-mongodb" not in message


def test_잘못된_MongoDB_URL_오류에도_입력값이_남지_않는다() -> None:
    with pytest.raises(DatabaseOperationError) as raised:
        create_mongo_client(
            SecretStr("mongodb://credential-marker@"),
            timeout_seconds=0.1,
        )

    assert "credential-marker" not in str(raised.value)


@pytest.mark.parametrize("database_name", ["smart_office", "dev_smart_office", "admin"])
def test_통합_테스트_database_이름은_test_접두사가_필수다(database_name: str) -> None:
    with pytest.raises(ValueError):
        validate_test_database_name(database_name)


def test_test_접두사_database_이름은_허용한다() -> None:
    validate_test_database_name("test_smart_office")
