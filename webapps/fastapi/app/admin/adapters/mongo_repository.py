"""Bounded read-only administrator queries over existing MongoDB collections."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import PyMongoError

from ...shared.database import MongoDatabase, MongoDocument
from ...shared.errors import RepositoryDataError, RepositoryUnavailableError
from ..models import (
    AlertSummary,
    AuditLogPage,
    AuditLogView,
    ClassroomSummary,
    DashboardActivity,
    DashboardActivityPage,
    DashboardActivityType,
    DashboardSnapshot,
    EmployeeSummary,
)


class MongoAdminDashboardRepository:
    """Queries source collections without creating a dashboard materialized view."""

    def __init__(self, database: MongoDatabase) -> None:
        self._employees = database["employees"]
        self._employee_history = database["employee_status_history"]
        self._classrooms = database["classrooms"]
        self._seats = database["seats"]
        self._seat_history = database["seat_occupancy_history"]
        self._alerts = database["after_hours_alerts"]
        self._audit = database["audit_logs"]

    @classmethod
    def ensure_indexes(cls, database: MongoDatabase) -> None:
        database["employee_status_history"].create_index(
            [("occurred_at", DESCENDING), ("_id", ASCENDING)],
            name="employee_history_dashboard_recent",
        )
        database["seat_occupancy_history"].create_index(
            [("observed_at", DESCENDING), ("_id", ASCENDING)],
            name="seat_history_dashboard_recent",
        )
        database["after_hours_alerts"].create_index(
            [("detected_at", DESCENDING), ("_id", ASCENDING)],
            name="alerts_dashboard_recent",
        )
        database["audit_logs"].create_index(
            [("occurred_at", DESCENDING), ("_id", ASCENDING)],
            name="audit_dashboard_recent",
        )
        database["audit_logs"].create_index(
            [("action", ASCENDING), ("occurred_at", DESCENDING)],
            name="audit_action_dashboard_recent",
        )

    def get_snapshot(
        self,
        *,
        department: str | None,
        classroom_id: str | None,
    ) -> DashboardSnapshot:
        try:
            employee_match: MongoDocument = {"is_active": True}
            if department is not None:
                employee_match["department"] = department
            employee_ids = [
                _string(item, "_id") for item in self._employees.find(employee_match, {"_id": 1})
            ]
            employee_counts = self._group_counts(
                self._employees,
                employee_match,
                "$current_status.status",
            )

            classroom_match: MongoDocument = {"is_active": True}
            if classroom_id is not None:
                classroom_match["_id"] = classroom_id
            classroom_ids = [
                _string(item, "_id") for item in self._classrooms.find(classroom_match, {"_id": 1})
            ]
            seat_match: MongoDocument = {
                "is_active": True,
                "classroom_id": {"$in": classroom_ids},
            }
            seat_counts = self._group_counts(
                self._seats,
                seat_match,
                "$current_occupancy.state",
            )
            alert_match: MongoDocument = {
                "status": "OPEN",
                "classroom_id": {"$in": classroom_ids},
            }
            return DashboardSnapshot(
                employees=EmployeeSummary(
                    total=len(employee_ids),
                    working=employee_counts.get("WORKING", 0),
                    on_call=employee_counts.get("ON_CALL", 0),
                    away=employee_counts.get("AWAY", 0),
                    offsite=employee_counts.get("OFFSITE", 0),
                ),
                classrooms=ClassroomSummary(
                    active=len(classroom_ids),
                    active_seats=sum(seat_counts.values()),
                    occupied_seats=seat_counts.get("OCCUPIED", 0),
                    unknown_seats=seat_counts.get("UNKNOWN", 0),
                ),
                alerts=AlertSummary(open_after_hours=self._alerts.count_documents(alert_match)),
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        except (KeyError, TypeError, ValueError):
            raise RepositoryDataError() from None

    @staticmethod
    def _group_counts(collection: Any, match: MongoDocument, group_field: str) -> dict[str, int]:
        documents = collection.aggregate(
            [
                {"$match": match},
                {"$group": {"_id": group_field, "count": {"$sum": 1}}},
            ]
        )
        return {
            str(item["_id"]): int(item["count"])
            for item in documents
            if item.get("_id") is not None
        }

    def list_activities(
        self,
        *,
        activity_type: DashboardActivityType | None,
        from_time: datetime,
        to_time: datetime,
        limit: int,
        offset: int,
    ) -> DashboardActivityPage:
        fetch_limit = offset + limit
        items: list[DashboardActivity] = []
        try:
            if activity_type in (None, DashboardActivityType.EMPLOYEE_STATUS):
                items.extend(self._employee_activities(from_time, to_time, fetch_limit))
            if activity_type in (None, DashboardActivityType.SEAT_OCCUPANCY):
                items.extend(self._seat_activities(from_time, to_time, fetch_limit))
            if activity_type in (None, DashboardActivityType.AFTER_HOURS_ALERT):
                items.extend(self._alert_activities(from_time, to_time, fetch_limit))
            total = sum(
                self._activity_count(kind, from_time, to_time)
                for kind in DashboardActivityType
                if activity_type in (None, kind)
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        except (KeyError, TypeError, ValueError):
            raise RepositoryDataError() from None
        items.sort(key=lambda item: (-item.occurred_at.timestamp(), item.id))
        return DashboardActivityPage(items=items[offset : offset + limit], total=total)

    def _activity_count(
        self, kind: DashboardActivityType, from_time: datetime, to_time: datetime
    ) -> int:
        collection, field = self._activity_source(kind)
        query: MongoDocument = {field: {"$gte": from_time, "$lt": to_time}}
        if kind == DashboardActivityType.SEAT_OCCUPANCY:
            query["state_changed"] = True
        return int(collection.count_documents(query))

    def _activity_source(self, kind: DashboardActivityType) -> tuple[Any, str]:
        return {
            DashboardActivityType.EMPLOYEE_STATUS: (
                self._employee_history,
                "occurred_at",
            ),
            DashboardActivityType.SEAT_OCCUPANCY: (self._seat_history, "observed_at"),
            DashboardActivityType.AFTER_HOURS_ALERT: (self._alerts, "detected_at"),
        }[kind]

    @staticmethod
    def _recent(
        collection: Any,
        field: str,
        from_time: datetime,
        to_time: datetime,
        limit: int,
        extra_match: MongoDocument | None = None,
    ) -> list[MongoDocument]:
        query: MongoDocument = {field: {"$gte": from_time, "$lt": to_time}}
        query.update(extra_match or {})
        cursor = collection.find(query)
        return list(cursor.sort([(field, DESCENDING), ("_id", ASCENDING)]).limit(limit))

    def _employee_activities(
        self, start: datetime, end: datetime, limit: int
    ) -> list[DashboardActivity]:
        return [
            DashboardActivity(
                id=f"employee:{_string(item, '_id')}",
                type=DashboardActivityType.EMPLOYEE_STATUS,
                occurred_at=_datetime(item, "occurred_at"),
                title="직원 상태 변경",
                description=f"{item.get('from_status') or '-'} → {_string(item, 'to_status')}",
                resource_type="employee",
                resource_id=_string(item, "employee_id"),
                target_route=f"/employees/{_string(item, 'employee_id')}",
            )
            for item in self._recent(self._employee_history, "occurred_at", start, end, limit)
        ]

    def _seat_activities(
        self, start: datetime, end: datetime, limit: int
    ) -> list[DashboardActivity]:
        return [
            DashboardActivity(
                id=f"seat:{_string(item, '_id')}",
                type=DashboardActivityType.SEAT_OCCUPANCY,
                occurred_at=_datetime(item, "observed_at"),
                title="좌석 상태 변경",
                description=f"{_string(item, 'from_state')} → {_string(item, 'to_state')}",
                resource_type="seat",
                resource_id=_string(item, "seat_id"),
                target_route=f"/classrooms/{_string(item, 'classroom_id')}",
            )
            for item in self._recent(
                self._seat_history,
                "observed_at",
                start,
                end,
                limit,
                {"state_changed": True},
            )
        ]

    def _alert_activities(
        self, start: datetime, end: datetime, limit: int
    ) -> list[DashboardActivity]:
        return [
            DashboardActivity(
                id=f"alert:{_string(item, '_id')}",
                type=DashboardActivityType.AFTER_HOURS_ALERT,
                occurred_at=_datetime(item, "detected_at"),
                title="마감 후 좌석 경고",
                description=f"좌석 {_string(item, 'seat_id')} · {_string(item, 'status')}",
                resource_type="after_hours_alert",
                resource_id=_string(item, "_id"),
                target_route="/admin#open-alerts",
            )
            for item in self._recent(self._alerts, "detected_at", start, end, limit)
        ]

    def list_audit_logs(
        self,
        *,
        actor_user_id: str | None,
        action: str | None,
        resource: str | None,
        from_time: datetime | None,
        to_time: datetime | None,
        limit: int,
        offset: int,
    ) -> AuditLogPage:
        query: MongoDocument = {}
        if actor_user_id is not None:
            query["actor_user_id"] = actor_user_id
        if action is not None:
            query["action"] = action
        if resource is not None:
            query["$or"] = [{"resource_type": resource}, {"resource_id": resource}]
        time_query: MongoDocument = {}
        if from_time is not None:
            time_query["$gte"] = from_time
        if to_time is not None:
            time_query["$lt"] = to_time
        if time_query:
            query["occurred_at"] = time_query
        try:
            total = self._audit.count_documents(query)
            cursor = (
                self._audit.find(query)
                .sort([("occurred_at", DESCENDING), ("_id", ASCENDING)])
                .skip(offset)
                .limit(limit)
            )
            items = [
                AuditLogView(
                    id=_string(item, "_id"),
                    actor_user_id=_optional_string(item, "actor_user_id"),
                    action=_string(item, "action"),
                    resource_type=_string(item, "resource_type"),
                    resource_id=_string(item, "resource_id"),
                    before=_mapping(item, "before"),
                    after=_mapping(item, "after"),
                    occurred_at=_datetime(item, "occurred_at"),
                )
                for item in cursor
            ]
            return AuditLogPage(items=items, total=total)
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        except (KeyError, TypeError, ValueError):
            raise RepositoryDataError() from None


def _string(document: MongoDocument, field: str) -> str:
    value = document[field]
    if not isinstance(value, str):
        raise TypeError
    return value


def _optional_string(document: MongoDocument, field: str) -> str | None:
    value = document.get(field)
    if value is not None and not isinstance(value, str):
        raise TypeError
    return value


def _datetime(document: MongoDocument, field: str) -> datetime:
    value = document[field]
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise TypeError
    return value


def _mapping(document: MongoDocument, field: str) -> dict[str, Any]:
    value = document[field]
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise TypeError
    return value
