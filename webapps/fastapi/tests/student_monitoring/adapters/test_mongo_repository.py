"""Mongo detection repository의 강의실 최근 이벤트 조회 계약."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import cast

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import OperationFailure

from app.shared.database import MongoDatabase, MongoDocument
from app.student_monitoring.adapters.mongo_repository import MongoDetectionEventRepository
from app.student_monitoring.models import DetectionEvent, FrameInfo


class FakeCursor:
    def __init__(self, documents: list[MongoDocument]) -> None:
        self.documents = documents
        self.sort_fields: list[tuple[str, int]] | None = None
        self.limit_value: int | None = None

    def sort(self, fields: list[tuple[str, int]]) -> FakeCursor:
        self.sort_fields = fields
        return self

    def limit(self, value: int) -> FakeCursor:
        self.limit_value = value
        return self

    def __iter__(self) -> Iterator[MongoDocument]:
        return iter(self.documents[: self.limit_value])


class FakeCollection:
    def __init__(self, documents: list[MongoDocument]) -> None:
        self.documents = documents
        self.query: MongoDocument | None = None
        self.cursor: FakeCursor | None = None

    def find(self, query: MongoDocument) -> FakeCursor:
        self.query = query
        self.cursor = FakeCursor(self.documents)
        return self.cursor


class FakeDatabase:
    def __init__(self, collection: FakeCollection) -> None:
        self.collection = collection

    def __getitem__(self, name: str) -> FakeCollection:
        assert name == "detection_events"
        return self.collection


def _event(event_id: str) -> DetectionEvent:
    captured_at = datetime(2026, 8, 13, 9, 9, tzinfo=UTC)
    return DetectionEvent(
        event_id=event_id,
        camera_id="camera-a",
        stream_id="stream-camera-a",
        classroom_id="classroom-a101",
        captured_at=captured_at,
        sequence=1,
        frame=FrameInfo(width_pixels=1920, height_pixels=1080),
        detections=(),
        received_at=captured_at,
        schema_version=1,
    )


def test_find_recent_by_classroom_uses_time_index_order_and_limit() -> None:
    document = MongoDetectionEventRepository._to_document(_event("event-a"))
    collection = FakeCollection([document])
    repository = MongoDetectionEventRepository(cast(MongoDatabase, FakeDatabase(collection)))
    since = datetime(2026, 8, 13, 9, 5, tzinfo=UTC)

    events = repository.find_recent_by_classroom(
        "classroom-a101",
        since,
        limit=25,
    )

    assert [event.event_id for event in events] == ["event-a"]
    assert collection.query == {
        "classroom_id": "classroom-a101",
        "captured_at": {"$gte": since},
    }
    assert collection.cursor is not None
    assert collection.cursor.sort_fields == [
        ("captured_at", DESCENDING),
        ("_id", ASCENDING),
    ]
    assert collection.cursor.limit_value == 25


class FakeIndexCollection:
    """`create_index` 호출과 옵션 충돌을 재현하는 대역."""

    def __init__(self, *, conflicting_index_names: frozenset[str] = frozenset()) -> None:
        self.indexes: list[tuple[list[tuple[str, int]], dict[str, object]]] = []
        self._conflicting_index_names = conflicting_index_names

    def create_index(self, keys: list[tuple[str, int]], **options: object) -> None:
        name = str(options.get("name", ""))
        if name in self._conflicting_index_names:
            # 같은 이름을 다른 옵션으로 만들려 할 때 MongoDB가 내는 실패다.
            raise OperationFailure("index options conflict", 85)
        self.indexes.append((keys, options))


class FakeIndexDatabase:
    def __init__(self, collection: FakeIndexCollection) -> None:
        self.collection = collection
        self.commands: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def __getitem__(self, name: str) -> FakeIndexCollection:
        assert name == "detection_events"
        return self.collection

    def command(self, *args: object, **kwargs: object) -> None:
        self.commands.append((args, kwargs))


def test_보존_기간을_TTL_index로_건다() -> None:
    collection = FakeIndexCollection()
    database = FakeIndexDatabase(collection)

    MongoDetectionEventRepository.ensure_indexes(cast(MongoDatabase, database), retention_days=3)

    by_name = {options["name"]: (keys, options) for keys, options in collection.indexes}
    assert by_name["detection_events_ttl"][0] == [("captured_at", ASCENDING)]
    # `expires_at` 필드를 새로 두지 않고 촬영 시각에 직접 건다. 이미 쌓인 문서도
    # 마이그레이션 없이 정리되게 하려는 선택이다.
    assert by_name["detection_events_ttl"][1]["expireAfterSeconds"] == 3 * 24 * 60 * 60
    assert "detection_events_camera_time" in by_name
    assert "detection_events_classroom_time" in by_name


def test_보존_기간이_바뀌면_index를_다시_만들지_않고_collMod로_고친다() -> None:
    # 이미 다른 기간으로 만들어져 있는 상태를 재현한다.
    collection = FakeIndexCollection(conflicting_index_names=frozenset({"detection_events_ttl"}))
    database = FakeIndexDatabase(collection)

    MongoDetectionEventRepository.ensure_indexes(cast(MongoDatabase, database), retention_days=14)

    # 인덱스를 지웠다 다시 만들면 그 사이 조회가 느려지므로 기간만 고쳐야 한다.
    assert len(database.commands) == 1
    args, kwargs = database.commands[0]
    assert args == ("collMod", "detection_events")
    assert kwargs["index"] == {
        "name": "detection_events_ttl",
        "expireAfterSeconds": 14 * 24 * 60 * 60,
    }
