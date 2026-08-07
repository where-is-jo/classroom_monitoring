"""MongoDB events 어댑터가 기존 EventRepository 계약을 지키는지 검증한다."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import cast

import pytest

from app.events.adapters.mongo_repository import MongoEventRepository
from app.shared.database import MongoDatabase, MongoDocument
from app.shared.errors import RepositoryDataError


class FakeCursor:
    def __init__(self, documents: list[MongoDocument]) -> None:
        self._documents = documents
        self._offset = 0
        self._limit = len(documents)

    def sort(self, field_name: str, direction: int) -> FakeCursor:
        self._documents.sort(
            key=lambda item: cast(datetime, item[field_name]), reverse=direction == -1
        )
        return self

    def skip(self, offset: int) -> FakeCursor:
        self._offset = offset
        return self

    def limit(self, limit: int) -> FakeCursor:
        self._limit = limit
        return self

    def __iter__(self) -> Iterator[MongoDocument]:
        return iter(self._documents[self._offset : self._offset + self._limit])


class FakeEventCollection:
    def __init__(self, documents: list[MongoDocument]) -> None:
        self.documents = documents

    def find(self, query: MongoDocument) -> FakeCursor:
        assert query == {}
        return FakeCursor(list(self.documents))

    def count_documents(self, query: MongoDocument) -> int:
        assert query == {}
        return len(self.documents)

    def find_one(self, query: MongoDocument) -> MongoDocument | None:
        return next(
            (document for document in self.documents if document["_id"] == query["_id"]),
            None,
        )


class FakeEventDatabase:
    def __init__(self, documents: list[MongoDocument]) -> None:
        self.collection = FakeEventCollection(documents)

    def __getitem__(self, collection_name: str) -> FakeEventCollection:
        assert collection_name == "events"
        return self.collection


def make_document(event_id: str, minute: int) -> MongoDocument:
    return {
        "_id": event_id,
        "camera_id": "cam-existing-01",
        "label": "person",
        "confidence": 0.9,
        "detected_at": datetime(2026, 8, 5, 9, minute, tzinfo=UTC),
    }


def _database(documents: list[MongoDocument]) -> MongoDatabase:
    return cast(MongoDatabase, FakeEventDatabase(documents))


def test_mongo_어댑터는_기존_목록_계약과_정렬을_지킨다() -> None:
    repository = MongoEventRepository(
        _database([make_document("evt-old", 0), make_document("evt-new", 10)])
    )

    events, total = repository.list_events(limit=1, offset=0)

    assert total == 2
    assert [event.id for event in events] == ["evt-new"]


def test_mongo_어댑터는_기존_상세_계약을_지킨다() -> None:
    repository = MongoEventRepository(_database([make_document("evt-001", 0)]))

    event = repository.get_event("evt-001")

    assert event is not None
    assert event.id == "evt-001"
    assert event.detected_at.tzinfo is not None
    assert repository.get_event("missing") is None


def test_잘못된_MongoDB_문서는_내부_값_없이_도메인_오류가_된다() -> None:
    malformed = make_document("evt-001", 0)
    malformed["detected_at"] = datetime(2026, 8, 5, 9, 0)  # noqa: DTZ001
    repository = MongoEventRepository(_database([malformed]))

    with pytest.raises(RepositoryDataError) as raised:
        repository.get_event("evt-001")

    assert "evt-001" not in str(raised.value)
