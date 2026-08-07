"""직원 집계와 상태 기록을 저장하는 동기 PyMongo 어댑터."""

from __future__ import annotations

import re
from datetime import datetime

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from ...shared.database import MongoDatabase, MongoDocument
from ...shared.errors import RepositoryDataError, RepositoryUnavailableError
from ..errors import (
    EmployeeNumberConflictError,
    EmployeeOperationConflictError,
    EmployeeUserLinkConflictError,
)
from ..models import (
    Employee,
    EmployeeCurrentStatus,
    EmployeeObservation,
    EmployeeOverride,
    EmployeePage,
    EmployeeStatus,
    EmployeeStatusHistory,
    EmployeeStatusHistoryPage,
    StatusSource,
)


class MongoEmployeeRepository:
    employee_collection_name = "employees"
    history_collection_name = "employee_status_history"
    observation_collection_name = "employee_observations"

    def __init__(self, database: MongoDatabase) -> None:
        self._employees = database[self.employee_collection_name]
        self._history = database[self.history_collection_name]
        self._observations = database[self.observation_collection_name]

    @classmethod
    def ensure_indexes(cls, database: MongoDatabase) -> None:
        employees = database[cls.employee_collection_name]
        employees.create_index(
            [("employee_no", ASCENDING)],
            name="employees_number_unique",
            unique=True,
        )
        employees.create_index(
            [("user_id", ASCENDING)],
            name="employees_user_sparse_unique",
            unique=True,
            sparse=True,
        )
        employees.create_index(
            [("operation_ids", ASCENDING)],
            name="employees_operation_unique",
            unique=True,
        )
        employees.create_index(
            [("is_active", ASCENDING), ("current_status.status", ASCENDING)],
            name="employees_active_status",
        )
        employees.create_index(
            [("department", ASCENDING), ("display_name", ASCENDING)],
            name="employees_department_name",
        )

        history = database[cls.history_collection_name]
        history.create_index(
            [("operation_id", ASCENDING)],
            name="employee_history_operation_unique",
            unique=True,
        )
        history.create_index(
            [("employee_id", ASCENDING), ("occurred_at", DESCENDING)],
            name="employee_history_employee_time",
        )

        observations = database[cls.observation_collection_name]
        observations.create_index(
            [("event_id", ASCENDING)],
            name="employee_observations_event_unique",
            unique=True,
        )
        observations.create_index(
            [("employee_id", ASCENDING), ("observed_at", DESCENDING)],
            name="employee_observations_employee_time",
        )
        observations.create_index(
            [
                ("employee_id", ASCENDING),
                ("person_present", ASCENDING),
                ("observed_at", DESCENDING),
            ],
            name="employee_observations_present_time",
        )

    def list_employees(
        self,
        *,
        limit: int,
        offset: int,
        search: str | None,
        department: str | None,
        status: EmployeeStatus | None,
        is_active: bool | None,
    ) -> EmployeePage:
        query: MongoDocument = {}
        if search:
            escaped = re.escape(search.strip())
            query["$or"] = [
                {"employee_no": {"$regex": escaped, "$options": "i"}},
                {"display_name": {"$regex": escaped, "$options": "i"}},
            ]
        if department:
            query["department"] = {
                "$regex": f"^{re.escape(department.strip())}$",
                "$options": "i",
            }
        if status is not None:
            query["current_status.status"] = status.value
        if is_active is not None:
            query["is_active"] = is_active
        try:
            cursor = (
                self._employees.find(query)
                .sort([("display_name", ASCENDING), ("_id", ASCENDING)])
                .skip(offset)
                .limit(limit)
            )
            return EmployeePage(
                items=[self._employee_to_domain(document) for document in cursor],
                total=self._employees.count_documents(query),
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def list_active_employees(self) -> list[Employee]:
        try:
            cursor = self._employees.find({"is_active": True}).sort("_id", ASCENDING)
            return [self._employee_to_domain(document) for document in cursor]
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def get_employee(self, employee_id: str) -> Employee | None:
        return self._find_employee({"_id": employee_id})

    def get_employee_by_number(self, employee_no: str) -> Employee | None:
        return self._find_employee({"employee_no": employee_no})

    def get_employee_by_user_id(self, user_id: str) -> Employee | None:
        return self._find_employee({"user_id": user_id})

    def get_employee_by_operation_id(self, operation_id: str) -> Employee | None:
        return self._find_employee({"operation_ids": operation_id})

    def create_employee(
        self,
        employee: Employee,
        initial_history: EmployeeStatusHistory,
    ) -> Employee:
        try:
            self._employees.insert_one(self._employee_to_document(employee))
        except DuplicateKeyError:
            operation_owner = self.get_employee_by_operation_id(employee.created_operation_id)
            if operation_owner is not None:
                if operation_owner.employee_no != employee.employee_no:
                    raise EmployeeOperationConflictError() from None
                self._append_history(initial_history)
                return operation_owner
            if self.get_employee_by_number(employee.employee_no) is not None:
                raise EmployeeNumberConflictError() from None
            if (
                employee.user_id is not None
                and self.get_employee_by_user_id(employee.user_id) is not None
            ):
                raise EmployeeUserLinkConflictError() from None
            raise RepositoryUnavailableError() from None
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        self._append_history(initial_history)
        return employee

    def replace_employee(
        self,
        employee: Employee,
        *,
        expected_version: int,
        history: EmployeeStatusHistory | None,
    ) -> Employee | None:
        operation_owner = self.get_employee_by_operation_id(employee.last_operation_id)
        if operation_owner is not None:
            if operation_owner.id != employee.id:
                raise EmployeeOperationConflictError()
            if history is not None:
                self._append_history(history)
            return operation_owner
        history_owner = self.get_history_by_operation_id(employee.last_operation_id)
        if history_owner is not None:
            if history_owner.employee_id != employee.id:
                raise EmployeeOperationConflictError()
            return self.get_employee(employee.id)

        document = self._employee_to_document(employee)
        document.pop("_id")
        try:
            updated = self._employees.find_one_and_update(
                {"_id": employee.id, "version": expected_version},
                {"$set": document},
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            operation_owner = self.get_employee_by_operation_id(employee.last_operation_id)
            if operation_owner is not None and operation_owner.id != employee.id:
                raise EmployeeOperationConflictError() from None
            number_owner = self.get_employee_by_number(employee.employee_no)
            if number_owner is not None and number_owner.id != employee.id:
                raise EmployeeNumberConflictError() from None
            if employee.user_id is not None:
                user_owner = self.get_employee_by_user_id(employee.user_id)
                if user_owner is not None and user_owner.id != employee.id:
                    raise EmployeeUserLinkConflictError() from None
            raise RepositoryUnavailableError() from None
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        if updated is None:
            return None
        if history is not None:
            self._append_history(history)
        return self._employee_to_domain(updated)

    def list_status_history(
        self,
        employee_id: str,
        *,
        limit: int,
        offset: int,
        source: StatusSource | None,
        from_status: EmployeeStatus | None,
        to_status: EmployeeStatus | None,
    ) -> EmployeeStatusHistoryPage:
        query: MongoDocument = {"employee_id": employee_id}
        if source is not None:
            query["source"] = source.value
        if from_status is not None:
            query["from_status"] = from_status.value
        if to_status is not None:
            query["to_status"] = to_status.value
        try:
            cursor = (
                self._history.find(query)
                .sort([("occurred_at", DESCENDING), ("_id", DESCENDING)])
                .skip(offset)
                .limit(limit)
            )
            return EmployeeStatusHistoryPage(
                items=[self._history_to_domain(document) for document in cursor],
                total=self._history.count_documents(query),
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def get_history_by_operation_id(
        self,
        operation_id: str,
    ) -> EmployeeStatusHistory | None:
        try:
            document = self._history.find_one({"operation_id": operation_id})
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else self._history_to_domain(document)

    def get_observation(self, event_id: str) -> EmployeeObservation | None:
        try:
            document = self._observations.find_one({"event_id": event_id})
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else self._observation_to_domain(document)

    def create_observation(
        self,
        observation: EmployeeObservation,
    ) -> EmployeeObservation:
        try:
            self._observations.insert_one(self._observation_to_document(observation))
            return observation
        except DuplicateKeyError:
            existing = self.get_observation(observation.event_id)
            if existing is not None:
                return existing
            raise RepositoryUnavailableError() from None
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def get_latest_observation(self, employee_id: str) -> EmployeeObservation | None:
        return self._find_latest_observation({"employee_id": employee_id})

    def get_latest_present_observation(
        self,
        employee_id: str,
    ) -> EmployeeObservation | None:
        return self._find_latest_observation({"employee_id": employee_id, "person_present": True})

    def _find_employee(self, query: MongoDocument) -> Employee | None:
        try:
            document = self._employees.find_one(query)
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else self._employee_to_domain(document)

    def _append_history(self, history: EmployeeStatusHistory) -> None:
        try:
            self._history.insert_one(self._history_to_document(history))
        except DuplicateKeyError:
            existing = self.get_history_by_operation_id(history.operation_id)
            if existing is None or existing.employee_id != history.employee_id:
                raise EmployeeOperationConflictError() from None
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def _find_latest_observation(
        self,
        query: MongoDocument,
    ) -> EmployeeObservation | None:
        try:
            document = self._observations.find_one(
                query,
                sort=[("observed_at", DESCENDING), ("received_at", DESCENDING)],
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else self._observation_to_domain(document)

    @staticmethod
    def _employee_to_document(employee: Employee) -> MongoDocument:
        current_status: MongoDocument = {
            "status": employee.current_status.status.value,
            "source": employee.current_status.source.value,
            "reason": employee.current_status.reason,
            "effective_at": employee.current_status.effective_at,
            "last_person_seen_at": employee.current_status.last_person_seen_at,
        }
        active_override: MongoDocument | None = None
        if employee.active_override is not None:
            active_override = {
                "status": employee.active_override.status.value,
                "reason": employee.active_override.reason,
                "actor_user_id": employee.active_override.actor_user_id,
                "starts_at": employee.active_override.starts_at,
                "ends_at": employee.active_override.ends_at,
            }
        document: MongoDocument = {
            "_id": employee.id,
            "employee_no": employee.employee_no,
            "display_name": employee.display_name,
            "department": employee.department,
            "position": employee.position,
            "office_zone": employee.office_zone,
            "is_active": employee.is_active,
            "current_status": current_status,
            "active_override": active_override,
            "created_at": employee.created_at,
            "updated_at": employee.updated_at,
            "version": employee.version,
            "created_operation_id": employee.created_operation_id,
            "last_operation_id": employee.last_operation_id,
            "operation_ids": list(employee.operation_ids),
        }
        if employee.user_id is not None:
            document["user_id"] = employee.user_id
        return document

    @staticmethod
    def _employee_to_domain(document: MongoDocument) -> Employee:
        try:
            current = _mapping(document, "current_status")
            override_document = document.get("active_override")
            active_override = None
            if override_document is not None:
                if not isinstance(override_document, dict):
                    raise TypeError("active_override must be a mapping")
                active_override = EmployeeOverride(
                    status=EmployeeStatus(_string(override_document, "status")),
                    reason=_string(override_document, "reason"),
                    actor_user_id=_string(override_document, "actor_user_id"),
                    starts_at=_aware_datetime(override_document, "starts_at"),
                    ends_at=_optional_aware_datetime(override_document, "ends_at"),
                )
            return Employee(
                id=_string(document, "_id"),
                employee_no=_string(document, "employee_no"),
                user_id=_optional_string(document, "user_id"),
                display_name=_string(document, "display_name"),
                department=_string(document, "department"),
                position=_string(document, "position"),
                office_zone=_string(document, "office_zone"),
                is_active=_boolean(document, "is_active"),
                current_status=EmployeeCurrentStatus(
                    status=EmployeeStatus(_string(current, "status")),
                    source=StatusSource(_string(current, "source")),
                    reason=_string(current, "reason"),
                    effective_at=_aware_datetime(current, "effective_at"),
                    last_person_seen_at=_optional_aware_datetime(current, "last_person_seen_at"),
                ),
                active_override=active_override,
                created_at=_aware_datetime(document, "created_at"),
                updated_at=_aware_datetime(document, "updated_at"),
                version=_integer(document, "version"),
                created_operation_id=_string(document, "created_operation_id"),
                last_operation_id=_string(document, "last_operation_id"),
                operation_ids=_string_tuple(document, "operation_ids"),
            )
        except (KeyError, TypeError, ValueError):
            raise RepositoryDataError() from None

    @staticmethod
    def _history_to_document(history: EmployeeStatusHistory) -> MongoDocument:
        return {
            "_id": history.id,
            "employee_id": history.employee_id,
            "from_status": (None if history.from_status is None else history.from_status.value),
            "to_status": history.to_status.value,
            "source": history.source.value,
            "reason": history.reason,
            "actor_user_id": history.actor_user_id,
            "operation_id": history.operation_id,
            "occurred_at": history.occurred_at,
        }

    @staticmethod
    def _history_to_domain(document: MongoDocument) -> EmployeeStatusHistory:
        try:
            from_status = document.get("from_status")
            if from_status is not None and not isinstance(from_status, str):
                raise TypeError("from_status must be a string or null")
            return EmployeeStatusHistory(
                id=_string(document, "_id"),
                employee_id=_string(document, "employee_id"),
                from_status=(None if from_status is None else EmployeeStatus(from_status)),
                to_status=EmployeeStatus(_string(document, "to_status")),
                source=StatusSource(_string(document, "source")),
                reason=_string(document, "reason"),
                actor_user_id=_optional_string(document, "actor_user_id"),
                operation_id=_string(document, "operation_id"),
                occurred_at=_aware_datetime(document, "occurred_at"),
            )
        except (KeyError, TypeError, ValueError):
            raise RepositoryDataError() from None

    @staticmethod
    def _observation_to_document(observation: EmployeeObservation) -> MongoDocument:
        return {
            "_id": observation.event_id,
            "event_id": observation.event_id,
            "employee_id": observation.employee_id,
            "person_present": observation.person_present,
            "phone_detected": observation.phone_detected,
            "confidence": observation.confidence,
            "observed_at": observation.observed_at,
            "received_at": observation.received_at,
            "resulting_status": observation.resulting_status.value,
            "status_changed": observation.status_changed,
            "source": StatusSource.MOCK.value,
        }

    @staticmethod
    def _observation_to_domain(document: MongoDocument) -> EmployeeObservation:
        try:
            confidence = document["confidence"]
            if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
                raise TypeError("confidence must be a number")
            if StatusSource(_string(document, "source")) is not StatusSource.MOCK:
                raise ValueError("observation source must be MOCK")
            return EmployeeObservation(
                event_id=_string(document, "event_id"),
                employee_id=_string(document, "employee_id"),
                person_present=_boolean(document, "person_present"),
                phone_detected=_boolean(document, "phone_detected"),
                confidence=float(confidence),
                observed_at=_aware_datetime(document, "observed_at"),
                received_at=_aware_datetime(document, "received_at"),
                resulting_status=EmployeeStatus(_string(document, "resulting_status")),
                status_changed=_boolean(document, "status_changed"),
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


def _string_tuple(document: MongoDocument, field: str) -> tuple[str, ...]:
    value = document[field]
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise TypeError(f"{field} must be a list of strings")
    return tuple(value)


def _mapping(document: MongoDocument, field: str) -> MongoDocument:
    value = document[field]
    if not isinstance(value, dict):
        raise TypeError(f"{field} must be a mapping")
    return value


def _boolean(document: MongoDocument, field: str) -> bool:
    value = document[field]
    if not isinstance(value, bool):
        raise TypeError(f"{field} must be a boolean")
    return value


def _integer(document: MongoDocument, field: str) -> int:
    value = document[field]
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{field} must be an integer")
    return value


def _aware_datetime(document: MongoDocument, field: str) -> datetime:
    value = document[field]
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise TypeError(f"{field} must be an aware datetime")
    return value


def _optional_aware_datetime(
    document: MongoDocument,
    field: str,
) -> datetime | None:
    value = document.get(field)
    if value is not None and (not isinstance(value, datetime) or value.tzinfo is None):
        raise TypeError(f"{field} must be an aware datetime or null")
    return value
