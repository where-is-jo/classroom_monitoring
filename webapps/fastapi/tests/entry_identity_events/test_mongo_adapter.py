from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pymongo.errors import DuplicateKeyError

from app.entry_identity_events.adapters.mongo import MongoEntryIdentityEventRepository
from app.entry_identity_events.errors import EntryIdentityEventConflictError
from app.entry_identity_events.models import (
    EntryFaceObservation,
    EntryFrameInfo,
    EntryIdentityEvent,
    EntryIdentityProcessingStatus,
    EntryIdentityStatus,
)
from app.shared.database import MongoDocument


class FakeCollection:
    def __init__(self) -> None:
        self.indexes: list[tuple[list[tuple[str, int]], dict[str, Any]]] = []
        self.documents: dict[str, MongoDocument] = {}

    def create_index(
        self,
        keys: list[tuple[str, int]],
        **kwargs: Any,
    ) -> None:
        self.indexes.append((keys, kwargs))

    def insert_one(self, document: MongoDocument) -> None:
        event_id = str(document["_id"])
        if event_id in self.documents:
            raise DuplicateKeyError("duplicate")
        self.documents[event_id] = deepcopy(document)

    def find_one(
        self,
        query: MongoDocument,
        projection: MongoDocument | None = None,
    ) -> MongoDocument | None:
        del projection
        document = self.documents.get(str(query.get("_id", "")))
        return None if document is None else deepcopy(document)


class FakeDatabase:
    def __init__(self) -> None:
        self.collection = FakeCollection()

    def __getitem__(self, name: str) -> FakeCollection:
        assert name == "entry_identity_events"
        return self.collection


def test_Mongo_TTL과_조회_index를_생성한다() -> None:
    database = FakeDatabase()

    MongoEntryIdentityEventRepository.ensure_indexes(database)  # type: ignore[arg-type]

    by_name = {options["name"]: (keys, options) for keys, options in database.collection.indexes}
    assert by_name["entry_identity_events_ttl"][0] == [("expires_at", 1)]
    assert by_name["entry_identity_events_ttl"][1]["expireAfterSeconds"] == 0
    assert "entry_identity_events_camera_time" in by_name
    assert "entry_identity_events_student_time" in by_name
    assert "entry_identity_events_status_time" in by_name


def event(*, quality: float = 0.8) -> EntryIdentityEvent:
    captured_at = datetime(2026, 8, 24, 8, 59, tzinfo=UTC)
    return EntryIdentityEvent(
        event_id="entry-camera-1787389140000-7-entry-face",
        camera_id="entry-camera",
        stream_id="entry-stream",
        captured_at=captured_at,
        sequence=7,
        frame=EntryFrameInfo(160, 120),
        processing_status=EntryIdentityProcessingStatus.SUCCEEDED,
        observations=(
            EntryFaceObservation(
                face_track_id="face-1",
                face_bbox=(40, 20, 80, 65),
                detection_confidence=0.94,
                identity_status=EntryIdentityStatus.REGISTERED,
                student_id="student-001",
                similarity=0.86,
                margin=0.31,
                quality=quality,
                observation_count=4,
                rejected_reason=None,
            ),
        ),
        received_at=captured_at + timedelta(minutes=1),
        expires_at=captured_at + timedelta(days=7),
    )


def test_Mongo_저장은_민감_필드_없이_멱등하고_다른_본문은_충돌한다() -> None:
    database = FakeDatabase()
    repository = MongoEntryIdentityEventRepository(database)  # type: ignore[arg-type]

    created, is_created = repository.save(event())
    duplicate, is_duplicate_created = repository.save(event())

    assert created == duplicate
    assert is_created is True
    assert is_duplicate_created is False
    stored = database.collection.documents[event().event_id]
    serialized = str(stored).lower()
    for forbidden in ("embedding", "jpeg", "student_name", "student_number"):
        assert forbidden not in serialized

    with pytest.raises(EntryIdentityEventConflictError):
        repository.save(event(quality=0.7))
