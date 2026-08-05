"""refresh token hash와 CAS rotation을 관리하는 PyMongo 어댑터."""

from __future__ import annotations

from datetime import datetime

from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError

from ...shared.database import MongoDatabase, MongoDocument
from ...shared.errors import RepositoryDataError, RepositoryUnavailableError
from ..models import RefreshRotationResult, RefreshRotationStatus, RefreshToken


class MongoAuthRepository:
    collection_name = "refresh_tokens"

    def __init__(self, database: MongoDatabase) -> None:
        self._collection = database[self.collection_name]

    @classmethod
    def ensure_indexes(cls, database: MongoDatabase) -> None:
        collection = database[cls.collection_name]
        collection.create_index(
            [("token_hash", ASCENDING)],
            name="refresh_tokens_hash_unique",
            unique=True,
        )
        collection.create_index(
            [("user_id", ASCENDING), ("expires_at", ASCENDING)],
            name="refresh_tokens_user_expiry",
        )
        collection.create_index(
            [("family_id", ASCENDING), ("revoked_at", ASCENDING)],
            name="refresh_tokens_family_revoked",
        )

    def create_refresh_token(self, refresh_token: RefreshToken) -> RefreshToken:
        try:
            self._collection.insert_one(self._to_document(refresh_token))
            return refresh_token
        except DuplicateKeyError:
            existing = self.get_refresh_token(refresh_token.token_hash)
            if existing is not None and existing.id == refresh_token.id:
                return existing
            raise RepositoryUnavailableError() from None
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def get_refresh_token(self, token_hash: str) -> RefreshToken | None:
        try:
            document = self._collection.find_one({"token_hash": token_hash})
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else self._to_domain(document)

    def rotate_refresh_token(
        self,
        *,
        current_token_hash: str,
        replacement: RefreshToken,
        now: datetime,
    ) -> RefreshRotationResult:
        current = self.get_refresh_token(current_token_hash)
        if current is None or current.expires_at <= now:
            return RefreshRotationResult(RefreshRotationStatus.INVALID, current)
        if current.revoked_at is not None:
            if current.replaced_by_id is not None:
                self.revoke_family(current.family_id, now=now)
                return RefreshRotationResult(RefreshRotationStatus.REUSED, current)
            return RefreshRotationResult(RefreshRotationStatus.INVALID, current)

        self.create_refresh_token(replacement)
        try:
            update_result = self._collection.update_one(
                {
                    "token_hash": current_token_hash,
                    "revoked_at": None,
                    "expires_at": {"$gt": now},
                },
                {
                    "$set": {
                        "revoked_at": now,
                        "replaced_by_id": replacement.id,
                    }
                },
            )
            if update_result.modified_count == 1:
                return RefreshRotationResult(RefreshRotationStatus.ROTATED, current)

            self._collection.update_one(
                {"token_hash": replacement.token_hash, "revoked_at": None},
                {"$set": {"revoked_at": now}},
            )
            self.revoke_family(current.family_id, now=now)
            return RefreshRotationResult(RefreshRotationStatus.REUSED, current)
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def revoke_family(self, family_id: str, *, now: datetime) -> None:
        try:
            self._collection.update_many(
                {"family_id": family_id, "revoked_at": None},
                {"$set": {"revoked_at": now}},
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def revoke_user_tokens(self, user_id: str, *, now: datetime) -> None:
        try:
            self._collection.update_many(
                {"user_id": user_id, "revoked_at": None},
                {"$set": {"revoked_at": now}},
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    @staticmethod
    def _to_document(refresh_token: RefreshToken) -> MongoDocument:
        return {
            "_id": refresh_token.id,
            "token_hash": refresh_token.token_hash,
            "user_id": refresh_token.user_id,
            "family_id": refresh_token.family_id,
            "expires_at": refresh_token.expires_at,
            "created_at": refresh_token.created_at,
            "revoked_at": refresh_token.revoked_at,
            "replaced_by_id": refresh_token.replaced_by_id,
        }

    @staticmethod
    def _to_domain(document: MongoDocument) -> RefreshToken:
        try:
            expires_at = document["expires_at"]
            created_at = document["created_at"]
            revoked_at = document.get("revoked_at")
            if any(
                not isinstance(value, datetime) or value.tzinfo is None
                for value in (expires_at, created_at)
            ):
                raise ValueError("refresh token timestamps must be aware")
            if revoked_at is not None and (
                not isinstance(revoked_at, datetime) or revoked_at.tzinfo is None
            ):
                raise ValueError("revoked_at must be aware")
            return RefreshToken(
                id=_string(document, "_id"),
                token_hash=_string(document, "token_hash"),
                user_id=_string(document, "user_id"),
                family_id=_string(document, "family_id"),
                expires_at=expires_at,
                created_at=created_at,
                revoked_at=revoked_at,
                replaced_by_id=_optional_string(document, "replaced_by_id"),
            )
        except (KeyError, TypeError, ValueError):
            raise RepositoryDataError() from None


def _string(document: MongoDocument, field: str) -> str:
    value = document[field]
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    return value


def _optional_string(document: MongoDocument, field: str) -> str | None:
    value = document.get(field)
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{field} must be a string or null")
    return value
