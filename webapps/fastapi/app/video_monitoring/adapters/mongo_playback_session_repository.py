"""MongoDB playback session repository."""

from __future__ import annotations

from pymongo import ASCENDING
from pymongo.errors import PyMongoError

from ...shared.database import MongoDatabase, MongoDocument
from ...shared.errors import RepositoryDataError, RepositoryUnavailableError
from ..models import PlaybackSession, PlaybackSessionStatus


class MongoPlaybackSessionRepository:
    """MongoDB playback session repository.

    TTL 만료 cleanup은 접근 시 lazy로 처리하므로 index는 조회·삭제 용도다.
    """

    collection_name = "playback_sessions"

    def __init__(self, database: MongoDatabase) -> None:
        self._collection = database[self.collection_name]

    @classmethod
    def ensure_indexes(cls, database: MongoDatabase) -> None:
        """Create indexes."""
        database[cls.collection_name].create_index(
            [("expires_at", ASCENDING)],
            name="playback_sessions_expires_at",
        )

    def save(self, session: PlaybackSession) -> PlaybackSession:
        """Save session (upsert by _id)."""
        try:
            self._collection.replace_one(
                {"_id": session.session_id},
                self._to_document(session),
                upsert=True,
            )
            return session
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def find_by_id(self, session_id: str) -> PlaybackSession | None:
        """Find session by ID."""
        try:
            document = self._collection.find_one({"_id": session_id})
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else self._to_domain(document)

    def delete_by_id(self, session_id: str) -> bool:
        """Delete session and return whether it existed."""
        try:
            result = self._collection.delete_one({"_id": session_id})
            return result.deleted_count > 0
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    @staticmethod
    def _to_document(session: PlaybackSession) -> MongoDocument:
        return {
            "_id": session.session_id,
            "stream_id": session.stream_id,
            "camera_id": session.camera_id,
            "status": session.status.value,
            "owner_token_hash": session.owner_token_hash,
            "expires_at": session.expires_at,
            "remote_resource_location": session.remote_resource_location,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
        }

    @staticmethod
    def _to_domain(document: MongoDocument) -> PlaybackSession:
        try:
            return PlaybackSession(
                session_id=str(document["_id"]),
                stream_id=str(document["stream_id"]),
                camera_id=str(document["camera_id"]),
                status=PlaybackSessionStatus(str(document["status"])),
                owner_token_hash=str(document["owner_token_hash"]),
                expires_at=document["expires_at"],
                remote_resource_location=document.get("remote_resource_location"),
                created_at=document["created_at"],
                updated_at=document["updated_at"],
            )
        except (KeyError, TypeError, ValueError):
            raise RepositoryDataError() from None
