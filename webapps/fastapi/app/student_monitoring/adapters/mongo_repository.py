"""MongoDB repository implementations."""

from __future__ import annotations

from datetime import datetime

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError

from ...shared.database import MongoDatabase, MongoDocument
from ...shared.errors import RepositoryDataError, RepositoryUnavailableError
from ..errors import InferenceEventConflictError
from ..models import (
    Detection,
    DetectionEvent,
    DetectionEventPage,
    FrameInfo,
    VideoSegment,
)


class MongoDetectionEventRepository:
    """MongoDB detection event repository."""

    collection_name = "detection_events"

    def __init__(self, database: MongoDatabase) -> None:
        self._collection = database[self.collection_name]

    @classmethod
    def ensure_indexes(cls, database: MongoDatabase) -> None:
        """Create indexes."""
        database[cls.collection_name].create_index(
            [("camera_id", ASCENDING), ("captured_at", DESCENDING)],
            name="detection_events_camera_time",
        )
        database[cls.collection_name].create_index(
            [("classroom_id", ASCENDING), ("captured_at", DESCENDING)],
            name="detection_events_classroom_time",
        )

    def save(self, event: DetectionEvent) -> DetectionEvent:
        """Save event (idempotent)."""
        try:
            self._collection.insert_one(self._to_document(event))
            return event
        except DuplicateKeyError:
            existing = self.find_by_event_id(event.event_id)
            if existing is None:
                raise InferenceEventConflictError() from None
            if (
                existing.camera_id != event.camera_id
                or existing.captured_at != event.captured_at
                or existing.sequence != event.sequence
                or existing.frame != event.frame
                or existing.detections != event.detections
            ):
                raise InferenceEventConflictError() from None
            return existing
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def find_by_event_id(self, event_id: str) -> DetectionEvent | None:
        """Find by event ID."""
        try:
            document = self._collection.find_one({"_id": event_id})
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else self._to_domain(document)

    def find_recent_by_camera(
        self, camera_id: str, limit: int
    ) -> list[DetectionEvent]:
        """Find recent detections by camera."""
        try:
            documents = list(
                self._collection.find({"camera_id": camera_id})
                .sort("captured_at", DESCENDING)
                .limit(limit)
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return [self._to_domain(doc) for doc in documents]

    def find_by_camera_and_period(
        self,
        camera_id: str,
        from_dt: datetime,
        to_dt: datetime,
        *,
        limit: int,
        cursor: str | None,
    ) -> DetectionEventPage:
        """Find detection events by camera and period."""
        query: MongoDocument = {
            "camera_id": camera_id,
            "captured_at": {"$gte": from_dt, "$lt": to_dt},
        }

        # Apply cursor
        if cursor:
            cursor_event = self.find_by_event_id(cursor)
            if cursor_event:
                query["captured_at"]["$lt"] = cursor_event.captured_at

        try:
            total = self._collection.count_documents(
                {"camera_id": camera_id, "captured_at": {"$gte": from_dt, "$lt": to_dt}}
            )
            documents = list(
                self._collection.find(query)
                .sort("captured_at", DESCENDING)
                .limit(limit)
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None

        items = [self._to_domain(doc) for doc in documents]
        next_cursor = items[-1].event_id if len(items) == limit and limit < total else None

        return DetectionEventPage(items=items, total=total, next_cursor=next_cursor)

    @staticmethod
    def _to_document(event: DetectionEvent) -> MongoDocument:
        return {
            "_id": event.event_id,
            "camera_id": event.camera_id,
            "stream_id": event.stream_id,
            "classroom_id": event.classroom_id,
            "captured_at": event.captured_at,
            "sequence": event.sequence,
            "frame": {
                "width_pixels": event.frame.width_pixels,
                "height_pixels": event.frame.height_pixels,
            },
            "detections": [
                {
                    "detection_id": d.detection_id,
                    "class_id": d.class_id,
                    "class_name": d.class_name,
                    "confidence": d.confidence,
                    "bbox": list(d.bbox),
                    "student_id": d.student_id,
                    "identity_confidence": d.identity_confidence,
                    "face_bbox": list(d.face_bbox) if d.face_bbox else None,
                }
                for d in event.detections
            ],
            "received_at": event.received_at,
            "schema_version": event.schema_version,
        }

    @staticmethod
    def _to_domain(document: MongoDocument) -> DetectionEvent:
        try:
            frame_doc = document["frame"]
            detections_doc = document["detections"]
            detections = tuple(
                Detection(
                    detection_id=str(d["detection_id"]),
                    class_id=int(d["class_id"]),
                    class_name=str(d["class_name"]),
                    confidence=float(d["confidence"]),
                    bbox=tuple(int(x) for x in d["bbox"]),
                    student_id=d.get("student_id"),
                    identity_confidence=d.get("identity_confidence"),
                    face_bbox=tuple(int(x) for x in d["face_bbox"]) if d.get("face_bbox") else None,
                )
                for d in detections_doc
            )
            return DetectionEvent(
                event_id=str(document["_id"]),
                camera_id=str(document["camera_id"]),
                stream_id=str(document["stream_id"]),
                classroom_id=str(document["classroom_id"]),
                captured_at=document["captured_at"],
                sequence=int(document["sequence"]),
                frame=FrameInfo(
                    width_pixels=int(frame_doc["width_pixels"]),
                    height_pixels=int(frame_doc["height_pixels"]),
                ),
                detections=detections,
                received_at=document["received_at"],
                schema_version=int(document["schema_version"]),
            )
        except (KeyError, TypeError, ValueError):
            raise RepositoryDataError() from None


class MongoVideoSegmentRepository:
    """MongoDB video segment repository."""

    collection_name = "video_segments"

    def __init__(self, database: MongoDatabase) -> None:
        self._collection = database[self.collection_name]

    @classmethod
    def ensure_indexes(cls, database: MongoDatabase) -> None:
        """Create indexes."""
        database[cls.collection_name].create_index(
            [("camera_id", ASCENDING), ("recorded_from", DESCENDING)],
            name="video_segments_camera_time",
        )
        database[cls.collection_name].create_index(
            [("classroom_id", ASCENDING), ("recorded_from", DESCENDING)],
            name="video_segments_classroom_time",
        )
        database[cls.collection_name].create_index(
            [("bucket_alias", ASCENDING), ("object_key", ASCENDING)],
            name="video_segments_bucket_key_unique",
            unique=True,
        )

    def save(self, segment: VideoSegment) -> VideoSegment:
        """Save segment (idempotent)."""
        try:
            self._collection.insert_one(self._to_document(segment))
            return segment
        except DuplicateKeyError:
            return segment
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def find_by_camera_and_period(
        self,
        camera_id: str,
        from_dt: datetime,
        to_dt: datetime,
        *,
        limit: int,
    ) -> list[VideoSegment]:
        """Find segments by camera and period."""
        try:
            documents = list(
                self._collection.find(
                    {
                        "camera_id": camera_id,
                        "recorded_from": {"$gte": from_dt, "$lt": to_dt},
                    }
                )
                .sort("recorded_from", DESCENDING)
                .limit(limit)
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return [self._to_domain(doc) for doc in documents]

    @staticmethod
    def _to_document(segment: VideoSegment) -> MongoDocument:
        return {
            "_id": segment.segment_id,
            "camera_id": segment.camera_id,
            "stream_id": segment.stream_id,
            "classroom_id": segment.classroom_id,
            "recorded_from": segment.recorded_from,
            "recorded_to": segment.recorded_to,
            "storage": segment.storage,
            "bucket_alias": segment.bucket_alias,
            "object_key": segment.object_key,
            "size_bytes": segment.size_bytes,
            "received_at": segment.received_at,
            "schema_version": segment.schema_version,
        }

    @staticmethod
    def _to_domain(document: MongoDocument) -> VideoSegment:
        try:
            return VideoSegment(
                segment_id=str(document["_id"]),
                camera_id=str(document["camera_id"]),
                stream_id=str(document["stream_id"]),
                classroom_id=str(document["classroom_id"]),
                recorded_from=document["recorded_from"],
                recorded_to=document["recorded_to"],
                storage=str(document["storage"]),
                bucket_alias=str(document["bucket_alias"]),
                object_key=str(document["object_key"]),
                size_bytes=int(document["size_bytes"]),
                received_at=document["received_at"],
                schema_version=int(document["schema_version"]),
            )
        except (KeyError, TypeError, ValueError):
            raise RepositoryDataError() from None
