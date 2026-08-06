"""UserRepository의 동기 PyMongo 구현."""

from __future__ import annotations

import re
from datetime import datetime

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from ...shared.database import MongoDatabase, MongoDocument
from ...shared.errors import RepositoryDataError, RepositoryUnavailableError
from ..errors import UserEmailConflictError, UserOperationConflictError
from ..models import User, UserPage, UserRole, UserStatus


class MongoUserRepository:
    collection_name = "users"

    def __init__(self, database: MongoDatabase) -> None:
        self._collection = database[self.collection_name]

    @classmethod
    def ensure_indexes(cls, database: MongoDatabase) -> None:
        collection = database[cls.collection_name]
        collection.create_index(
            [("email", ASCENDING)],
            name="users_email_unique",
            unique=True,
        )
        collection.create_index(
            [("operation_ids", ASCENDING)],
            name="users_operation_unique",
            unique=True,
        )
        collection.create_index(
            [("role", ASCENDING), ("status", ASCENDING)],
            name="users_role_status",
        )

    def list_users(
        self,
        *,
        limit: int,
        offset: int,
        role: UserRole | None,
        status: UserStatus | None,
        search: str | None,
    ) -> UserPage:
        query: MongoDocument = {}
        if role is not None:
            query["role"] = role.value
        if status is not None:
            query["status"] = status.value
        if search:
            escaped_search = re.escape(search.strip())
            query["$or"] = [
                {"email": {"$regex": escaped_search, "$options": "i"}},
                {"name": {"$regex": escaped_search, "$options": "i"}},
            ]
        try:
            cursor = (
                self._collection.find(query)
                .sort([("created_at", DESCENDING), ("_id", DESCENDING)])
                .skip(offset)
                .limit(limit)
            )
            users = [self._to_domain(document) for document in cursor]
            return UserPage(items=users, total=self._collection.count_documents(query))
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def get_user(self, user_id: str) -> User | None:
        return self._find_one({"_id": user_id})

    def get_user_by_email(self, email: str) -> User | None:
        return self._find_one({"email": email})

    def get_user_by_operation_id(self, operation_id: str) -> User | None:
        return self._find_one({"operation_ids": operation_id})

    def create_user(self, user: User) -> User:
        try:
            self._collection.insert_one(self._to_document(user))
            return user
        except DuplicateKeyError:
            existing_operation = self.get_user_by_operation_id(user.created_operation_id)
            if existing_operation is not None:
                if existing_operation.email != user.email:
                    raise UserOperationConflictError() from None
                return existing_operation
            raise UserEmailConflictError() from None
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def replace_user(self, user: User, *, expected_version: int) -> User | None:
        document = self._to_document(user)
        document.pop("_id")
        try:
            updated = self._collection.find_one_and_update(
                {"_id": user.id, "version": expected_version},
                {"$set": document},
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            operation_owner = self.get_user_by_operation_id(user.last_operation_id)
            if operation_owner is not None and operation_owner.id != user.id:
                raise UserOperationConflictError() from None
            raise UserEmailConflictError() from None
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if updated is None else self._to_domain(updated)

    def _find_one(self, query: MongoDocument) -> User | None:
        try:
            document = self._collection.find_one(query)
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else self._to_domain(document)

    @staticmethod
    def _to_document(user: User) -> MongoDocument:
        return {
            "_id": user.id,
            "email": user.email,
            "password_hash": user.password_hash,
            "name": user.name,
            "role": user.role.value,
            "status": user.status.value,
            "failed_login_count": user.failed_login_count,
            "locked_until": user.locked_until,
            "last_login_at": user.last_login_at,
            "created_at": user.created_at,
            "updated_at": user.updated_at,
            "version": user.version,
            "created_operation_id": user.created_operation_id,
            "last_operation_id": user.last_operation_id,
            "must_change_password": user.must_change_password,
            "password_changed_at": user.password_changed_at,
            "operation_ids": list(
                dict.fromkeys([user.created_operation_id, user.last_operation_id])
            ),
        }

    @staticmethod
    def _to_domain(document: MongoDocument) -> User:
        try:
            datetime_fields = ("created_at", "updated_at")
            if any(
                not isinstance(document[field], datetime) or document[field].tzinfo is None
                for field in datetime_fields
            ):
                raise ValueError("user timestamps must be aware")
            for optional_field in ("locked_until", "last_login_at", "password_changed_at"):
                value = document.get(optional_field)
                if value is not None and (not isinstance(value, datetime) or value.tzinfo is None):
                    raise ValueError("optional user timestamp must be aware")
            return User(
                id=_required_string(document, "_id"),
                email=_required_string(document, "email"),
                password_hash=_required_string(document, "password_hash"),
                name=_required_string(document, "name"),
                role=UserRole(_required_string(document, "role")),
                status=UserStatus(_required_string(document, "status")),
                failed_login_count=_required_int(document, "failed_login_count"),
                locked_until=document.get("locked_until"),
                last_login_at=document.get("last_login_at"),
                created_at=document["created_at"],
                updated_at=document["updated_at"],
                version=_required_int(document, "version"),
                created_operation_id=_required_string(document, "created_operation_id"),
                last_operation_id=_required_string(document, "last_operation_id"),
                must_change_password=_optional_bool(document, "must_change_password", False),
                password_changed_at=document.get("password_changed_at"),
            )
        except (KeyError, TypeError, ValueError):
            raise RepositoryDataError() from None


def _required_string(document: MongoDocument, field: str) -> str:
    value = document[field]
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    return value


def _required_int(document: MongoDocument, field: str) -> int:
    value = document[field]
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{field} must be an integer")
    return value


def _optional_bool(document: MongoDocument, field: str, default: bool) -> bool:
    value = document.get(field, default)
    if not isinstance(value, bool):
        raise TypeError(f"{field} must be a boolean")
    return value
