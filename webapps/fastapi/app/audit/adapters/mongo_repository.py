"""sanitized audit log를 저장하는 PyMongo 어댑터."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError

from ...shared.database import MongoDatabase, MongoDocument
from ...shared.errors import RepositoryDataError, RepositoryUnavailableError
from ..errors import AuditOperationConflictError
from ..models import AuditLog


class MongoAuditRepository:
    collection_name = "audit_logs"

    def __init__(self, database: MongoDatabase) -> None:
        self._collection = database[self.collection_name]

    @classmethod
    def ensure_indexes(cls, database: MongoDatabase) -> None:
        collection = database[cls.collection_name]
        collection.create_index(
            [("operation_id", ASCENDING)],
            name="audit_logs_operation_unique",
            unique=True,
        )
        collection.create_index(
            [("actor_user_id", ASCENDING), ("occurred_at", DESCENDING)],
            name="audit_logs_actor_time",
        )
        collection.create_index(
            [
                ("resource_type", ASCENDING),
                ("resource_id", ASCENDING),
                ("occurred_at", DESCENDING),
            ],
            name="audit_logs_resource_time",
        )

    def append(self, audit_log: AuditLog) -> AuditLog:
        try:
            self._collection.insert_one(self._to_document(audit_log))
            return audit_log
        except DuplicateKeyError:
            existing = self.get_by_operation_id(audit_log.operation_id)
            if existing is not None:
                if (
                    existing.action != audit_log.action
                    or existing.resource_type != audit_log.resource_type
                    or existing.resource_id != audit_log.resource_id
                ):
                    raise AuditOperationConflictError() from None
                return existing
            raise RepositoryUnavailableError() from None
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def get_by_operation_id(self, operation_id: str) -> AuditLog | None:
        try:
            document = self._collection.find_one({"operation_id": operation_id})
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else self._to_domain(document)

    @staticmethod
    def _to_document(audit_log: AuditLog) -> MongoDocument:
        return {
            "_id": audit_log.id,
            "operation_id": audit_log.operation_id,
            "actor_user_id": audit_log.actor_user_id,
            "action": audit_log.action,
            "resource_type": audit_log.resource_type,
            "resource_id": audit_log.resource_id,
            "before": audit_log.before,
            "after": audit_log.after,
            "ip_fingerprint": audit_log.ip_fingerprint,
            "occurred_at": audit_log.occurred_at,
        }

    @staticmethod
    def _to_domain(document: MongoDocument) -> AuditLog:
        try:
            occurred_at = document["occurred_at"]
            if not isinstance(occurred_at, datetime) or occurred_at.tzinfo is None:
                raise ValueError("occurred_at must be aware")
            before = _mapping(document, "before")
            after = _mapping(document, "after")
            return AuditLog(
                id=_string(document, "_id"),
                operation_id=_string(document, "operation_id"),
                actor_user_id=_optional_string(document, "actor_user_id"),
                action=_string(document, "action"),
                resource_type=_string(document, "resource_type"),
                resource_id=_string(document, "resource_id"),
                before=before,
                after=after,
                ip_fingerprint=_optional_string(document, "ip_fingerprint"),
                occurred_at=occurred_at,
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


def _mapping(document: MongoDocument, field: str) -> dict[str, Any]:
    value = document[field]
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise TypeError(f"{field} must be a mapping")
    return value
