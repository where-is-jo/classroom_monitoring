"""Mongo detection repository의 강의실 최근 이벤트 조회 계약."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import cast

from pymongo import ASCENDING, DESCENDING

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
