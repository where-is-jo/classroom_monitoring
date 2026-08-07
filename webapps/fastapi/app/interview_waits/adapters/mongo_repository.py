"""면담 대기와 전이 이력 PyMongo 어댑터."""

from __future__ import annotations

from datetime import datetime

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from ...shared.database import MongoDatabase, MongoDocument
from ...shared.errors import RepositoryDataError, RepositoryUnavailableError
from ..errors import InterviewWaitDuplicateError, InterviewWaitOperationConflictError
from ..models import (
    ACTIVE_WAIT_STATUSES,
    InterviewWait,
    InterviewWaitHistory,
    InterviewWaitPage,
    InterviewWaitStatus,
)


class MongoInterviewWaitRepository:
    wait_collection_name = "interview_waits"
    history_collection_name = "interview_wait_history"

    def __init__(self, database: MongoDatabase) -> None:
        self._waits = database[self.wait_collection_name]
        self._history = database[self.history_collection_name]

    @classmethod
    def ensure_indexes(cls, database: MongoDatabase) -> None:
        waits = database[cls.wait_collection_name]
        waits.create_index(
            [("active_key", ASCENDING)],
            name="interview_waits_active_key_unique",
            unique=True,
            sparse=True,
        )
        waits.create_index(
            [("operation_ids", ASCENDING)],
            name="interview_waits_operation_unique",
            unique=True,
        )
        waits.create_index(
            [("employee_id", ASCENDING), ("status", ASCENDING)],
            name="interview_waits_employee_status",
        )
        waits.create_index(
            [("requester_user_id", ASCENDING), ("status", ASCENDING)],
            name="interview_waits_requester_status",
        )
        waits.create_index(
            [("expires_at", ASCENDING), ("status", ASCENDING)],
            name="interview_waits_expiration_status",
        )
        database[cls.history_collection_name].create_index(
            [("operation_id", ASCENDING)],
            name="interview_wait_history_operation_unique",
            unique=True,
        )
        database[cls.history_collection_name].create_index(
            [("wait_id", ASCENDING), ("occurred_at", ASCENDING)],
            name="interview_wait_history_wait_time",
        )

    def create_wait(self, wait: InterviewWait, history: InterviewWaitHistory) -> InterviewWait:
        try:
            self._waits.insert_one(self._wait_to_document(wait))
        except DuplicateKeyError:
            operation_owner = self.get_wait_by_operation_id(wait.created_operation_id)
            if operation_owner is not None:
                self.append_history(history)
                return operation_owner
            active_owner = self.get_active_wait(wait.requester_user_id, wait.employee_id)
            if active_owner is not None:
                raise InterviewWaitDuplicateError() from None
            raise RepositoryUnavailableError() from None
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        self.append_history(history)
        return wait

    def get_wait(self, wait_id: str) -> InterviewWait | None:
        return self._find_wait({"_id": wait_id})

    def get_wait_by_operation_id(self, operation_id: str) -> InterviewWait | None:
        return self._find_wait({"operation_ids": operation_id})

    def get_active_wait(self, requester_user_id: str, employee_id: str) -> InterviewWait | None:
        return self._find_wait({"active_key": f"{requester_user_id}:{employee_id}"})

    def _find_wait(self, query: MongoDocument) -> InterviewWait | None:
        try:
            document = self._waits.find_one(query)
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else self._wait_to_domain(document)

    def list_waits(
        self,
        *,
        requester_user_id: str | None,
        employee_id: str | None,
        status: InterviewWaitStatus | None,
        limit: int,
        offset: int,
    ) -> InterviewWaitPage:
        query: MongoDocument = {}
        if requester_user_id is not None:
            query["requester_user_id"] = requester_user_id
        if employee_id is not None:
            query["employee_id"] = employee_id
        if status is not None:
            query["status"] = status.value
        try:
            total = self._waits.count_documents(query)
            documents = list(
                self._waits.find(query)
                .sort([("requested_at", DESCENDING), ("_id", DESCENDING)])
                .skip(offset)
                .limit(limit)
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return InterviewWaitPage(
            items=[self._wait_to_domain(document) for document in documents],
            total=total,
        )

    def list_active_for_employee(self, employee_id: str) -> list[InterviewWait]:
        try:
            documents = list(
                self._waits.find(
                    {
                        "employee_id": employee_id,
                        "status": {"$in": [status.value for status in ACTIVE_WAIT_STATUSES]},
                    }
                ).sort([("requested_at", ASCENDING), ("_id", ASCENDING)])
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return [self._wait_to_domain(document) for document in documents]

    def list_expired_candidates(self, now: datetime) -> list[InterviewWait]:
        try:
            documents = list(
                self._waits.find(
                    {
                        "status": {"$in": [status.value for status in ACTIVE_WAIT_STATUSES]},
                        "expires_at": {"$lte": now},
                    }
                ).sort([("expires_at", ASCENDING), ("_id", ASCENDING)])
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return [self._wait_to_domain(document) for document in documents]

    def replace_wait(
        self,
        wait: InterviewWait,
        *,
        expected_version: int,
        history: InterviewWaitHistory,
    ) -> InterviewWait | None:
        try:
            document = self._waits.find_one_and_replace(
                {"_id": wait.id, "version": expected_version},
                self._wait_to_document(wait),
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            raise InterviewWaitDuplicateError() from None
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        if document is None:
            operation_owner = self.get_wait_by_operation_id(history.operation_id)
            if operation_owner is None or operation_owner.id != wait.id:
                return None
            self.append_history(history)
            return operation_owner
        self.append_history(history)
        return self._wait_to_domain(document)

    def append_history(self, history: InterviewWaitHistory) -> InterviewWaitHistory:
        try:
            self._history.insert_one(self._history_to_document(history))
            return history
        except DuplicateKeyError:
            existing = self.get_history_by_operation_id(history.operation_id)
            if existing is not None:
                if existing.wait_id != history.wait_id or existing.to_status != history.to_status:
                    raise InterviewWaitOperationConflictError() from None
                return existing
            raise RepositoryUnavailableError() from None
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def get_history_by_operation_id(self, operation_id: str) -> InterviewWaitHistory | None:
        try:
            document = self._history.find_one({"operation_id": operation_id})
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else self._history_to_domain(document)

    def list_history(self, wait_id: str) -> list[InterviewWaitHistory]:
        try:
            documents = list(
                self._history.find({"wait_id": wait_id}).sort(
                    [("occurred_at", ASCENDING), ("_id", ASCENDING)]
                )
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return [self._history_to_domain(document) for document in documents]

    @staticmethod
    def _wait_to_document(wait: InterviewWait) -> MongoDocument:
        document: MongoDocument = {
            "_id": wait.id,
            "requester_user_id": wait.requester_user_id,
            "employee_id": wait.employee_id,
            "status": wait.status.value,
            "message": wait.message,
            "requested_at": wait.requested_at,
            "ready_at": wait.ready_at,
            "completed_at": wait.completed_at,
            "cancelled_at": wait.cancelled_at,
            "expires_at": wait.expires_at,
            "version": wait.version,
            "created_operation_id": wait.created_operation_id,
            "last_operation_id": wait.last_operation_id,
            "operation_ids": list(wait.operation_ids),
            "last_actor_user_id": wait.last_actor_user_id,
        }
        if wait.active_key is not None:
            document["active_key"] = wait.active_key
        return document

    @staticmethod
    def _wait_to_domain(document: MongoDocument) -> InterviewWait:
        try:
            operation_ids = document["operation_ids"]
            if not isinstance(operation_ids, list) or any(
                not isinstance(item, str) for item in operation_ids
            ):
                raise TypeError("operation_ids must be a string list")
            version = document["version"]
            if not isinstance(version, int) or isinstance(version, bool) or version < 0:
                raise TypeError("version must be a non-negative integer")
            return InterviewWait(
                id=_string(document, "_id"),
                requester_user_id=_string(document, "requester_user_id"),
                employee_id=_string(document, "employee_id"),
                status=InterviewWaitStatus(_string(document, "status")),
                message=_optional_string(document, "message"),
                requested_at=_aware_datetime(document, "requested_at"),
                ready_at=_optional_aware_datetime(document, "ready_at"),
                completed_at=_optional_aware_datetime(document, "completed_at"),
                cancelled_at=_optional_aware_datetime(document, "cancelled_at"),
                expires_at=_aware_datetime(document, "expires_at"),
                version=version,
                active_key=_optional_string(document, "active_key"),
                created_operation_id=_string(document, "created_operation_id"),
                last_operation_id=_string(document, "last_operation_id"),
                operation_ids=tuple(operation_ids),
                last_actor_user_id=_optional_string(document, "last_actor_user_id"),
            )
        except (KeyError, TypeError, ValueError):
            raise RepositoryDataError() from None

    @staticmethod
    def _history_to_document(history: InterviewWaitHistory) -> MongoDocument:
        return {
            "_id": history.id,
            "wait_id": history.wait_id,
            "from_status": None if history.from_status is None else history.from_status.value,
            "to_status": history.to_status.value,
            "reason": history.reason,
            "actor_user_id": history.actor_user_id,
            "operation_id": history.operation_id,
            "occurred_at": history.occurred_at,
        }

    @staticmethod
    def _history_to_domain(document: MongoDocument) -> InterviewWaitHistory:
        try:
            from_status = _optional_string(document, "from_status")
            return InterviewWaitHistory(
                id=_string(document, "_id"),
                wait_id=_string(document, "wait_id"),
                from_status=None if from_status is None else InterviewWaitStatus(from_status),
                to_status=InterviewWaitStatus(_string(document, "to_status")),
                reason=_string(document, "reason"),
                actor_user_id=_optional_string(document, "actor_user_id"),
                operation_id=_string(document, "operation_id"),
                occurred_at=_aware_datetime(document, "occurred_at"),
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


def _aware_datetime(document: MongoDocument, field: str) -> datetime:
    value = document[field]
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise TypeError(f"{field} must be an aware datetime")
    return value


def _optional_aware_datetime(document: MongoDocument, field: str) -> datetime | None:
    value = document.get(field)
    if value is not None and (not isinstance(value, datetime) or value.tzinfo is None):
        raise TypeError(f"{field} must be an aware datetime or null")
    return value
