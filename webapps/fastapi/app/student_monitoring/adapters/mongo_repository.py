"""MongoDB repository implementations."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError, OperationFailure, PyMongoError

from ...shared.database import MongoDatabase, MongoDocument
from ...shared.errors import RepositoryDataError, RepositoryUnavailableError
from ..errors import InferenceEventConflictError
from ..models import (
    Detection,
    DetectionEvent,
    DetectionEventPage,
    FrameInfo,
    StudentState,
    StudentStateHistory,
    StudentStateReason,
    StudentStateRecord,
    VideoSegment,
)

# 같은 이름의 인덱스를 다른 옵션으로 만들려 할 때 MongoDB가 내는 코드.
# 85는 IndexOptionsConflict, 86은 IndexKeySpecsConflict다.
_INDEX_OPTIONS_CONFLICT_CODES = frozenset({85, 86})


def _to_bbox(value: Any) -> tuple[int, int, int, int]:
    """저장된 bbox 값을 길이 4 튜플로 좁힌다.

    `tuple(int(x) for x in ...)`는 `tuple[int, ...]`라 길이를 보장하지 못한다.
    도메인 모델은 좌표 4개를 요구하므로 여기서 실제로 4개인지 확인한다.
    길이가 다르면 언패킹이 ValueError를 내고, `_to_domain`의 예외 처리가
    이를 RepositoryDataError로 바꾼다 — 저장된 데이터가 깨진 것이므로
    잘못된 도메인 객체를 만들어 넘기는 것보다 낫다.
    """
    x1, y1, x2, y2 = (int(coordinate) for coordinate in value)
    return (x1, y1, x2, y2)


class MongoDetectionEventRepository:
    """MongoDB detection event repository."""

    collection_name = "detection_events"
    ttl_index_name = "detection_events_ttl"

    def __init__(self, database: MongoDatabase) -> None:
        self._collection = database[self.collection_name]

    @classmethod
    def ensure_indexes(cls, database: MongoDatabase, *, retention_days: int = 7) -> None:
        """Create indexes."""
        database[cls.collection_name].create_index(
            [("camera_id", ASCENDING), ("captured_at", DESCENDING)],
            name="detection_events_camera_time",
        )
        database[cls.collection_name].create_index(
            [("classroom_id", ASCENDING), ("captured_at", DESCENDING)],
            name="detection_events_classroom_time",
        )
        cls._ensure_ttl_index(database, retention_days=retention_days)

    @classmethod
    def _ensure_ttl_index(cls, database: MongoDatabase, *, retention_days: int) -> None:
        """보존 기간이 지난 탐지 이벤트를 MongoDB가 스스로 지우게 한다.

        **`expires_at` 필드를 새로 두지 않고 `captured_at`에 직접 건다.** 입구 관측은
        문서에 만료 시각을 담지만(`entry_identity_events_ttl`), 탐지 이벤트는 이미
        쌓인 문서가 많아 필드를 추가하면 마이그레이션이 필요하다. 촬영 시각 기준
        TTL은 기존 문서에도 그대로 적용된다.

        대신 보존 기간을 바꾸면 인덱스 옵션이 달라진다. pymongo는 같은 이름의
        인덱스를 다른 옵션으로 만들려 하면 거절하므로, 그때는 `collMod`로 기간만
        고친다. 인덱스를 지웠다 다시 만들면 그 사이 조회가 느려진다.
        """
        expire_after_seconds = retention_days * 24 * 60 * 60
        try:
            database[cls.collection_name].create_index(
                [("captured_at", ASCENDING)],
                name=cls.ttl_index_name,
                expireAfterSeconds=expire_after_seconds,
            )
        except OperationFailure as error:
            if error.code not in _INDEX_OPTIONS_CONFLICT_CODES:
                raise
            database.command(
                "collMod",
                cls.collection_name,
                index={
                    "name": cls.ttl_index_name,
                    "expireAfterSeconds": expire_after_seconds,
                },
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

    def find_recent_by_camera(self, camera_id: str, limit: int) -> list[DetectionEvent]:
        """Find recent detections by camera."""
        try:
            documents = list(
                self._collection.find({"camera_id": camera_id})
                .sort([("captured_at", DESCENDING), ("_id", ASCENDING)])
                .limit(limit)
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return [self._to_domain(doc) for doc in documents]

    def find_recent_by_classroom(
        self,
        classroom_id: str,
        since: datetime,
        *,
        limit: int,
    ) -> list[DetectionEvent]:
        """Find recent detections for a classroom."""
        try:
            documents = list(
                self._collection.find(
                    {
                        "classroom_id": classroom_id,
                        "captured_at": {"$gte": since},
                    }
                )
                .sort([("captured_at", DESCENDING), ("_id", ASCENDING)])
                .limit(limit)
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return [self._to_domain(document) for document in documents]

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
                self._collection.find(query).sort("captured_at", DESCENDING).limit(limit)
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
                    "track_id": d.track_id,
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
                    bbox=_to_bbox(d["bbox"]),
                    student_id=d.get("student_id"),
                    identity_confidence=d.get("identity_confidence"),
                    face_bbox=_to_bbox(d["face_bbox"]) if d.get("face_bbox") else None,
                    track_id=d.get("track_id"),
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


class MongoStudentStateRepository:
    """MongoDB student state repository.

    상태는 학생당 하나이므로 `(classroom_id, student_id)`를 문서 키로 쓰고 upsert한다.
    이력은 별도 collection에 쌓으며 `_id`를 event 기반으로 만들어 재수신에서 두 번
    쌓이지 않게 한다.
    """

    collection_name = "student_states"
    history_collection_name = "student_state_history"

    def __init__(self, database: MongoDatabase) -> None:
        self._collection = database[self.collection_name]
        self._history = database[self.history_collection_name]

    @classmethod
    def ensure_indexes(cls, database: MongoDatabase) -> None:
        """Create indexes."""
        database[cls.collection_name].create_index(
            [("classroom_id", ASCENDING)],
            name="student_states_classroom",
        )
        database[cls.history_collection_name].create_index(
            [
                ("classroom_id", ASCENDING),
                ("student_id", ASCENDING),
                ("observed_at", DESCENDING),
            ],
            name="student_state_history_classroom_student_time",
        )

    def list_by_classroom(self, classroom_id: str) -> list[StudentStateRecord]:
        try:
            documents = list(self._collection.find({"classroom_id": classroom_id}))
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return [self._to_domain(document) for document in documents]

    def save(self, record: StudentStateRecord) -> StudentStateRecord:
        try:
            self._collection.replace_one(
                {"_id": self._key(record.classroom_id, record.student_id)},
                self._to_document(record),
                upsert=True,
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return record

    def append_history(self, history: StudentStateHistory) -> StudentStateHistory:
        try:
            self._history.insert_one(self._history_document(history))
        except DuplicateKeyError:
            # 같은 event_id 재수신. 이력은 이미 있으므로 그대로 둔다.
            return history
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return history

    def list_history(
        self, classroom_id: str, student_id: str, *, limit: int
    ) -> list[StudentStateHistory]:
        try:
            documents = list(
                self._history.find({"classroom_id": classroom_id, "student_id": student_id})
                .sort([("observed_at", DESCENDING), ("_id", ASCENDING)])
                .limit(limit)
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return [self._history_to_domain(document) for document in documents]

    @staticmethod
    def _key(classroom_id: str, student_id: str) -> str:
        return f"{classroom_id}:{student_id}"

    @classmethod
    def _to_document(cls, record: StudentStateRecord) -> MongoDocument:
        return {
            "_id": cls._key(record.classroom_id, record.student_id),
            "classroom_id": record.classroom_id,
            "student_id": record.student_id,
            "state": record.state.value,
            "reason": record.reason.value,
            "seat_id": record.seat_id,
            "assigned_seat_id": record.assigned_seat_id,
            "confidence": record.confidence,
            "observed_at": record.observed_at,
            "event_id": record.event_id,
            "identified_at": record.identified_at,
            "vacant_since": record.vacant_since,
        }

    @staticmethod
    def _to_domain(document: MongoDocument) -> StudentStateRecord:
        try:
            return StudentStateRecord(
                student_id=str(document["student_id"]),
                classroom_id=str(document["classroom_id"]),
                state=StudentState(document["state"]),
                reason=StudentStateReason(document["reason"]),
                seat_id=document.get("seat_id"),
                assigned_seat_id=document.get("assigned_seat_id"),
                confidence=document.get("confidence"),
                observed_at=document["observed_at"],
                event_id=str(document["event_id"]),
                identified_at=document.get("identified_at"),
                vacant_since=document.get("vacant_since"),
            )
        except (KeyError, TypeError, ValueError):
            raise RepositoryDataError() from None

    @staticmethod
    def _history_document(history: StudentStateHistory) -> MongoDocument:
        return {
            "_id": history.id,
            "classroom_id": history.classroom_id,
            "student_id": history.student_id,
            "event_id": history.event_id,
            "from_state": history.from_state.value,
            "to_state": history.to_state.value,
            "reason": history.reason.value,
            "seat_id": history.seat_id,
            "confidence": history.confidence,
            "observed_at": history.observed_at,
            "recorded_at": history.recorded_at,
        }

    @staticmethod
    def _history_to_domain(document: MongoDocument) -> StudentStateHistory:
        try:
            return StudentStateHistory(
                id=str(document["_id"]),
                student_id=str(document["student_id"]),
                classroom_id=str(document["classroom_id"]),
                event_id=str(document["event_id"]),
                from_state=StudentState(document["from_state"]),
                to_state=StudentState(document["to_state"]),
                reason=StudentStateReason(document["reason"]),
                seat_id=document.get("seat_id"),
                confidence=document.get("confidence"),
                observed_at=document["observed_at"],
                recorded_at=document["recorded_at"],
            )
        except (KeyError, TypeError, ValueError):
            raise RepositoryDataError() from None
