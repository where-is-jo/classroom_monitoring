"""MongoDB video stream repository."""

from __future__ import annotations

from datetime import datetime

from pymongo import ASCENDING
from pymongo.errors import PyMongoError

from ...shared.database import MongoDatabase, MongoDocument
from ...shared.errors import RepositoryDataError, RepositoryUnavailableError
from ..models import CameraRole, PlaybackKind, VideoStream


class MongoVideoStreamRepository:
    """MongoDB video stream repository."""

    collection_name = "video_streams"

    def __init__(self, database: MongoDatabase) -> None:
        self._collection = database[self.collection_name]

    @classmethod
    def ensure_indexes(cls, database: MongoDatabase) -> None:
        """Create indexes."""
        database[cls.collection_name].create_index(
            [("camera_id", ASCENDING)],
            name="video_streams_camera_id_unique",
            unique=True,
        )
        database[cls.collection_name].create_index(
            [("classroom_id", ASCENDING), ("enabled", ASCENDING)],
            name="video_streams_classroom_enabled",
        )

    def find_by_id(self, stream_id: str) -> VideoStream | None:
        """Find stream by ID."""
        try:
            document = self._collection.find_one({"_id": stream_id})
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else self._to_domain(document)

    def find_by_camera_id(self, camera_id: str) -> VideoStream | None:
        """Find stream by camera ID."""
        try:
            document = self._collection.find_one({"camera_id": camera_id})
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else self._to_domain(document)

    def find_all_enabled(self) -> list[VideoStream]:
        """Find all enabled streams."""
        try:
            documents = list(self._collection.find({"enabled": True}).sort("camera_id", ASCENDING))
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return [self._to_domain(doc) for doc in documents]

    def find_monitoring_streams(self) -> list[VideoStream]:
        """실제 모니터링 stream만 반환한다 (enabled=true AND is_demo=false)."""
        try:
            documents = list(
                self._collection.find({"enabled": True, "is_demo": False}).sort(
                    "camera_id", ASCENDING
                )
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return [self._to_domain(doc) for doc in documents]

    def update_last_detection(self, camera_id: str, captured_at: datetime) -> None:
        """마지막 탐지 시각을 과거로 되돌리지 않고 갱신한다."""
        try:
            self._collection.update_one(
                {"camera_id": camera_id},
                {"$max": {"last_detection_at": captured_at}},
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def save(self, stream: VideoStream) -> VideoStream:
        """Save stream."""
        try:
            self._collection.update_one(
                {"camera_id": stream.camera_id},
                {"$set": self._to_document(stream)},
                upsert=True,
            )
            return stream
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    @staticmethod
    def _to_document(stream: VideoStream) -> MongoDocument:
        return {
            "_id": stream.id,
            "camera_id": stream.camera_id,
            "classroom_id": stream.classroom_id,
            "camera_label": stream.camera_label,
            "playback_kind": stream.playback_kind.value,
            "playback_path": stream.playback_path,
            "enabled": stream.enabled,
            "last_frame_at": stream.last_frame_at,
            "last_detection_at": stream.last_detection_at,
            "is_demo": stream.is_demo,
            "role": stream.role.value,
            "created_at": stream.created_at,
            "updated_at": stream.updated_at,
        }

    @staticmethod
    def _to_domain(document: MongoDocument) -> VideoStream:
        try:
            return VideoStream(
                id=str(document["_id"]),
                camera_id=str(document["camera_id"]),
                classroom_id=str(document["classroom_id"]),
                camera_label=str(document["camera_label"]),
                playback_kind=PlaybackKind(str(document["playback_kind"])),
                playback_path=document.get("playback_path"),
                enabled=bool(document["enabled"]),
                last_frame_at=document.get("last_frame_at"),
                last_detection_at=document.get("last_detection_at"),
                is_demo=bool(document["is_demo"]),
                # 역할이 없는 기존 문서는 좌석 판정 카메라로 읽는다.
                role=CameraRole(str(document.get("role", CameraRole.SEAT_JUDGING.value))),
                created_at=document["created_at"],
                updated_at=document["updated_at"],
            )
        except (KeyError, TypeError, ValueError):
            raise RepositoryDataError() from None
