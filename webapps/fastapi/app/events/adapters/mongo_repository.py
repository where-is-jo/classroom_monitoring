"""기존 ``EventRepository`` 계약을 구현하는 동기 PyMongo 어댑터."""

from __future__ import annotations

from datetime import datetime

from pymongo import DESCENDING
from pymongo.errors import PyMongoError

from ...shared.database import MongoDatabase, MongoDocument
from ...shared.errors import RepositoryDataError, RepositoryUnavailableError
from ..models import Event


class MongoEventRepository:
    """MongoDB 문서를 events 도메인 모델로 변환하는 읽기 저장소."""

    collection_name = "events"
    detected_at_index_name = "events_detected_at_desc"

    def __init__(self, database: MongoDatabase) -> None:
        self._collection = database[self.collection_name]

    @classmethod
    def ensure_indexes(cls, database: MongoDatabase) -> None:
        """목록 정렬용 index를 고정 이름으로 idempotent하게 만든다."""
        database[cls.collection_name].create_index(
            [("detected_at", DESCENDING)],
            name=cls.detected_at_index_name,
        )

    def list_events(self, *, limit: int, offset: int) -> tuple[list[Event], int]:
        try:
            cursor = (
                self._collection.find({})
                .sort("detected_at", DESCENDING)
                .skip(offset)
                .limit(limit)
            )
            events = [self._to_domain(document) for document in cursor]
            total = self._collection.count_documents({})
            return events, total
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def get_event(self, event_id: str) -> Event | None:
        try:
            document = self._collection.find_one({"_id": event_id})
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        if document is None:
            return None
        return self._to_domain(document)

    @staticmethod
    def _to_domain(document: MongoDocument) -> Event:
        try:
            detected_at = document["detected_at"]
            if not isinstance(detected_at, datetime) or detected_at.tzinfo is None:
                raise ValueError("detected_at must be an aware datetime")

            event_id = document["_id"]
            camera_id = document["camera_id"]
            label = document["label"]
            confidence = document["confidence"]
            if not isinstance(event_id, str):
                raise TypeError("_id must be a string")
            if not isinstance(camera_id, str) or not isinstance(label, str):
                raise TypeError("event text fields must be strings")
            if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
                raise TypeError("confidence must be numeric")
            return Event(
                id=event_id,
                camera_id=camera_id,
                label=label,
                confidence=float(confidence),
                detected_at=detected_at,
            )
        except (KeyError, TypeError, ValueError):
            raise RepositoryDataError() from None
