"""입구 얼굴 관측 이벤트의 MongoDB 저장소."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError

from ...shared.database import MongoDatabase, MongoDocument, document_id
from ...shared.errors import RepositoryDataError, RepositoryUnavailableError
from ..errors import EntryIdentityEventConflictError
from ..models import (
    EntryFaceObservation,
    EntryFrameInfo,
    EntryIdentityEvent,
    EntryIdentityEventPage,
    EntryIdentityProcessingStatus,
    EntryIdentityStatus,
    same_event_body,
)


class MongoEntryIdentityEventRepository:
    collection_name = "entry_identity_events"

    def __init__(self, database: MongoDatabase) -> None:
        self._collection = database[self.collection_name]

    @classmethod
    def ensure_indexes(cls, database: MongoDatabase) -> None:
        collection = database[cls.collection_name]
        collection.create_index(
            [("expires_at", ASCENDING)],
            name="entry_identity_events_ttl",
            expireAfterSeconds=0,
        )
        collection.create_index(
            [("camera_id", ASCENDING), ("captured_at", DESCENDING)],
            name="entry_identity_events_camera_time",
        )
        collection.create_index(
            [("observations.student_id", ASCENDING), ("captured_at", DESCENDING)],
            name="entry_identity_events_student_time",
        )
        collection.create_index(
            [
                ("observations.identity_status", ASCENDING),
                ("captured_at", DESCENDING),
            ],
            name="entry_identity_events_status_time",
        )

    def save(self, event: EntryIdentityEvent) -> tuple[EntryIdentityEvent, bool]:
        try:
            self._collection.insert_one(self._to_document(event))
            return event, True
        except DuplicateKeyError:
            try:
                document = self._collection.find_one({"_id": event.event_id})
            except PyMongoError:
                raise RepositoryUnavailableError() from None
            if document is None:
                raise RepositoryUnavailableError() from None
            existing = self._to_domain(document)
            if not same_event_body(existing, event):
                raise EntryIdentityEventConflictError() from None
            return existing, False
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def list_by_stream(
        self,
        stream_id: str,
        *,
        status: EntryIdentityStatus | None,
        student_id: str | None,
        from_at: datetime | None,
        to_at: datetime | None,
        limit: int,
        cursor: str | None,
    ) -> EntryIdentityEventPage:
        query = self._query(
            stream_id,
            status=status,
            student_id=student_id,
            from_at=from_at,
            to_at=to_at,
        )
        try:
            total = self._collection.count_documents(query)
            if cursor is not None:
                cursor_document = self._collection.find_one(
                    {"_id": cursor, "stream_id": stream_id},
                    {"captured_at": 1},
                )
                if cursor_document is None:
                    return EntryIdentityEventPage([], total, None)
                cursor_captured_at = cursor_document.get("captured_at")
                if not isinstance(cursor_captured_at, datetime):
                    raise RepositoryDataError()
                query["$or"] = [
                    {"captured_at": {"$lt": cursor_captured_at}},
                    {
                        "captured_at": cursor_captured_at,
                        "_id": {"$lt": cursor},
                    },
                ]
            documents = list(
                self._collection.find(query)
                .sort([("captured_at", DESCENDING), ("_id", DESCENDING)])
                .limit(limit + 1)
            )
        except RepositoryDataError:
            raise
        except PyMongoError:
            raise RepositoryUnavailableError() from None

        has_more = len(documents) > limit
        items = [self._to_domain(document) for document in documents[:limit]]
        next_cursor = items[-1].event_id if has_more and items else None
        return EntryIdentityEventPage(items, total, next_cursor)

    @staticmethod
    def _query(
        stream_id: str,
        *,
        status: EntryIdentityStatus | None,
        student_id: str | None,
        from_at: datetime | None,
        to_at: datetime | None,
    ) -> MongoDocument:
        query: MongoDocument = {"stream_id": stream_id}
        captured_filter: dict[str, datetime] = {}
        if from_at is not None:
            captured_filter["$gte"] = from_at
        if to_at is not None:
            captured_filter["$lte"] = to_at
        if captured_filter:
            query["captured_at"] = captured_filter
        if status is not None and student_id is not None:
            query["observations"] = {
                "$elemMatch": {
                    "identity_status": status.value,
                    "student_id": student_id,
                }
            }
        elif status is not None:
            query["observations.identity_status"] = status.value
        elif student_id is not None:
            query["observations.student_id"] = student_id
        return query

    @staticmethod
    def _to_document(event: EntryIdentityEvent) -> MongoDocument:
        return {
            "_id": event.event_id,
            "camera_id": event.camera_id,
            "stream_id": event.stream_id,
            "captured_at": event.captured_at,
            "sequence": event.sequence,
            "frame": {
                "width_pixels": event.frame.width_pixels,
                "height_pixels": event.frame.height_pixels,
            },
            "processing_status": event.processing_status.value,
            "observations": [
                {
                    "face_track_id": item.face_track_id,
                    "face_bbox": list(item.face_bbox),
                    "detection_confidence": item.detection_confidence,
                    "identity_status": item.identity_status.value,
                    "student_id": item.student_id,
                    "similarity": item.similarity,
                    "margin": item.margin,
                    "quality": item.quality,
                    "observation_count": item.observation_count,
                    "rejected_reason": item.rejected_reason,
                }
                for item in event.observations
            ],
            "received_at": event.received_at,
            "expires_at": event.expires_at,
        }

    @staticmethod
    def _to_domain(document: MongoDocument) -> EntryIdentityEvent:
        try:
            frame = document["frame"]
            observations = document["observations"]
            if (
                not isinstance(frame, dict)
                or not isinstance(observations, list)
                or any(not isinstance(item, dict) for item in observations)
            ):
                raise TypeError
            return EntryIdentityEvent(
                event_id=document_id(document),
                camera_id=str(document["camera_id"]),
                stream_id=str(document["stream_id"]),
                captured_at=_datetime(document["captured_at"]),
                sequence=int(document["sequence"]),
                frame=EntryFrameInfo(
                    int(frame["width_pixels"]),
                    int(frame["height_pixels"]),
                ),
                processing_status=EntryIdentityProcessingStatus(document["processing_status"]),
                observations=tuple(
                    EntryFaceObservation(
                        face_track_id=str(item["face_track_id"]),
                        face_bbox=tuple(int(value) for value in item["face_bbox"]),  # type: ignore[arg-type]
                        detection_confidence=float(item["detection_confidence"]),
                        identity_status=EntryIdentityStatus(item["identity_status"]),
                        student_id=(
                            None if item.get("student_id") is None else str(item["student_id"])
                        ),
                        similarity=(
                            None if item.get("similarity") is None else float(item["similarity"])
                        ),
                        margin=(None if item.get("margin") is None else float(item["margin"])),
                        quality=float(item["quality"]),
                        observation_count=int(item["observation_count"]),
                        rejected_reason=(
                            None
                            if item.get("rejected_reason") is None
                            else str(item["rejected_reason"])
                        ),
                    )
                    for item in observations
                ),
                received_at=_datetime(document["received_at"]),
                expires_at=_datetime(document["expires_at"]),
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            raise RepositoryDataError() from None


def _datetime(value: Any) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError
    return value
