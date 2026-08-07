"""PyMongo adapter for classrooms, seats, observations, and alerts."""

from __future__ import annotations

from datetime import date, datetime, time
from math import isfinite

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from ...shared.database import MongoDatabase, MongoDocument
from ...shared.errors import RepositoryDataError, RepositoryUnavailableError
from ..errors import (
    ClassroomDuplicateError,
    SeatBatchConflictError,
    SeatDuplicateError,
)
from ..models import (
    AfterHoursAlert,
    AfterHoursAlertPage,
    AfterHoursAlertStatus,
    Classroom,
    ClassroomPage,
    ClassroomSchedule,
    ObservationBatchStatus,
    OccupancySource,
    Seat,
    SeatCurrentOccupancy,
    SeatGeometry,
    SeatObservation,
    SeatObservationBatchRecord,
    SeatOccupancy,
    SeatOccupancyHistory,
    SeatOccupancyHistoryPage,
    SeatPage,
)


class MongoClassroomRepository:
    classroom_collection_name = "classrooms"
    seat_collection_name = "seats"
    batch_collection_name = "seat_observation_batches"
    history_collection_name = "seat_occupancy_history"
    alert_collection_name = "after_hours_alerts"

    def __init__(self, database: MongoDatabase) -> None:
        self._classrooms = database[self.classroom_collection_name]
        self._seats = database[self.seat_collection_name]
        self._batches = database[self.batch_collection_name]
        self._history = database[self.history_collection_name]
        self._alerts = database[self.alert_collection_name]

    @classmethod
    def ensure_indexes(cls, database: MongoDatabase) -> None:
        database[cls.classroom_collection_name].create_index(
            [("code", ASCENDING)], name="classrooms_code_unique", unique=True
        )
        database[cls.classroom_collection_name].create_index(
            [("operation_ids", ASCENDING)],
            name="classrooms_operation_unique",
            unique=True,
        )
        database[cls.classroom_collection_name].create_index(
            [("is_active", ASCENDING), ("code", ASCENDING)],
            name="classrooms_active_code",
        )
        database[cls.seat_collection_name].create_index(
            [("classroom_id", ASCENDING), ("code", ASCENDING)],
            name="seats_classroom_code_unique",
            unique=True,
        )
        database[cls.seat_collection_name].create_index(
            [("operation_ids", ASCENDING)],
            name="seats_operation_unique",
            unique=True,
        )
        database[cls.seat_collection_name].create_index(
            [
                ("classroom_id", ASCENDING),
                ("is_active", ASCENDING),
                ("current_occupancy.state", ASCENDING),
            ],
            name="seats_classroom_active_occupancy",
        )
        database[cls.batch_collection_name].create_index(
            [("classroom_id", ASCENDING), ("observed_at", DESCENDING)],
            name="seat_observation_batches_classroom_time",
        )
        database[cls.history_collection_name].create_index(
            [("event_id", ASCENDING), ("seat_id", ASCENDING)],
            name="seat_occupancy_history_event_seat_unique",
            unique=True,
        )
        database[cls.history_collection_name].create_index(
            [("classroom_id", ASCENDING), ("observed_at", DESCENDING)],
            name="seat_occupancy_history_classroom_time",
        )
        database[cls.history_collection_name].create_index(
            [("seat_id", ASCENDING), ("observed_at", DESCENDING)],
            name="seat_occupancy_history_seat_time",
        )
        database[cls.alert_collection_name].create_index(
            [("dedupe_key", ASCENDING)],
            name="after_hours_alerts_dedupe_unique",
            unique=True,
        )
        database[cls.alert_collection_name].create_index(
            [("operation_ids", ASCENDING)],
            name="after_hours_alerts_operation_unique",
            unique=True,
        )
        database[cls.alert_collection_name].create_index(
            [
                ("status", ASCENDING),
                ("classroom_id", ASCENDING),
                ("business_date", DESCENDING),
                ("detected_at", DESCENDING),
            ],
            name="after_hours_alerts_filters",
        )

    def create_classroom(self, classroom: Classroom) -> Classroom:
        try:
            self._classrooms.insert_one(self._classroom_to_document(classroom))
            return classroom
        except DuplicateKeyError:
            operation_owner = self.get_classroom_by_operation_id(classroom.created_operation_id)
            if operation_owner is not None:
                return operation_owner
            if self.get_classroom_by_code(classroom.code) is not None:
                raise ClassroomDuplicateError() from None
            raise RepositoryUnavailableError() from None
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def get_classroom(self, classroom_id: str) -> Classroom | None:
        return self._find_classroom({"_id": classroom_id})

    def get_classroom_by_code(self, code: str) -> Classroom | None:
        return self._find_classroom({"code": code})

    def get_classroom_by_operation_id(self, operation_id: str) -> Classroom | None:
        return self._find_classroom({"operation_ids": operation_id})

    def _find_classroom(self, query: MongoDocument) -> Classroom | None:
        try:
            document = self._classrooms.find_one(query)
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else self._classroom_to_domain(document)

    def list_classrooms(self, *, include_inactive: bool, limit: int, offset: int) -> ClassroomPage:
        query: MongoDocument = {} if include_inactive else {"is_active": True}
        try:
            total = self._classrooms.count_documents(query)
            documents = list(
                self._classrooms.find(query)
                .sort([("code", ASCENDING), ("_id", ASCENDING)])
                .skip(offset)
                .limit(limit)
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return ClassroomPage(
            items=[self._classroom_to_domain(item) for item in documents],
            total=total,
        )

    def replace_classroom(self, classroom: Classroom, *, expected_version: int) -> Classroom | None:
        try:
            document = self._classrooms.find_one_and_replace(
                {"_id": classroom.id, "version": expected_version},
                self._classroom_to_document(classroom),
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            raise ClassroomDuplicateError() from None
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        if document is None:
            owner = self.get_classroom_by_operation_id(classroom.last_operation_id)
            return owner if owner and owner.id == classroom.id else None
        return self._classroom_to_domain(document)

    def create_seat(self, seat: Seat) -> Seat:
        try:
            self._seats.insert_one(self._seat_to_document(seat))
            return seat
        except DuplicateKeyError:
            operation_owner = self.get_seat_by_operation_id(seat.created_operation_id)
            if operation_owner is not None:
                return operation_owner
            if self._find_seat({"classroom_id": seat.classroom_id, "code": seat.code}) is not None:
                raise SeatDuplicateError() from None
            raise RepositoryUnavailableError() from None
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def get_seat(self, seat_id: str) -> Seat | None:
        return self._find_seat({"_id": seat_id})

    def get_seat_by_operation_id(self, operation_id: str) -> Seat | None:
        return self._find_seat({"operation_ids": operation_id})

    def _find_seat(self, query: MongoDocument) -> Seat | None:
        try:
            document = self._seats.find_one(query)
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else self._seat_to_domain(document)

    def list_seats(
        self,
        classroom_id: str,
        *,
        include_inactive: bool,
        limit: int,
        offset: int,
    ) -> SeatPage:
        query: MongoDocument = {"classroom_id": classroom_id}
        if not include_inactive:
            query["is_active"] = True
        try:
            total = self._seats.count_documents(query)
            documents = list(
                self._seats.find(query)
                .sort([("code", ASCENDING), ("_id", ASCENDING)])
                .skip(offset)
                .limit(limit)
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return SeatPage(items=[self._seat_to_domain(item) for item in documents], total=total)

    def replace_seat(self, seat: Seat, *, expected_version: int) -> Seat | None:
        try:
            document = self._seats.find_one_and_replace(
                {"_id": seat.id, "version": expected_version},
                self._seat_to_document(seat),
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            raise SeatDuplicateError() from None
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        if document is None:
            owner = self.get_seat_by_operation_id(seat.last_operation_id)
            return owner if owner and owner.id == seat.id else None
        return self._seat_to_domain(document)

    def claim_observation_batch(
        self, record: SeatObservationBatchRecord
    ) -> SeatObservationBatchRecord:
        try:
            self._batches.insert_one(self._batch_to_document(record))
            return record
        except DuplicateKeyError:
            existing = self.get_observation_batch(record.event_id)
            if existing is not None and _same_batch(existing, record):
                return existing
            raise SeatBatchConflictError() from None
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def get_observation_batch(self, event_id: str) -> SeatObservationBatchRecord | None:
        try:
            document = self._batches.find_one({"_id": event_id})
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else self._batch_to_domain(document)

    def complete_observation_batch(
        self, record: SeatObservationBatchRecord
    ) -> SeatObservationBatchRecord:
        existing = self.get_observation_batch(record.event_id)
        if existing is None or not _same_batch(existing, record):
            raise SeatBatchConflictError()
        if existing.status == ObservationBatchStatus.COMPLETED:
            return existing
        try:
            document = self._batches.find_one_and_replace(
                {"_id": record.event_id, "status": ObservationBatchStatus.PROCESSING.value},
                self._batch_to_document(record),
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        if document is None:
            completed = self.get_observation_batch(record.event_id)
            if completed is None or not _same_batch(completed, record):
                raise SeatBatchConflictError()
            return completed
        return self._batch_to_domain(document)

    def get_history_by_event_and_seat(
        self, event_id: str, seat_id: str
    ) -> SeatOccupancyHistory | None:
        try:
            document = self._history.find_one({"event_id": event_id, "seat_id": seat_id})
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else self._history_to_domain(document)

    def append_occupancy_history(self, history: SeatOccupancyHistory) -> SeatOccupancyHistory:
        try:
            self._history.insert_one(self._history_to_document(history))
            return history
        except DuplicateKeyError:
            existing = self.get_history_by_event_and_seat(history.event_id, history.seat_id)
            if existing == history:
                return existing
            raise SeatBatchConflictError() from None
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def list_occupancy_history(
        self,
        classroom_id: str,
        *,
        seat_id: str | None,
        from_time: datetime | None,
        to_time: datetime | None,
        limit: int,
        offset: int,
    ) -> SeatOccupancyHistoryPage:
        query: MongoDocument = {"classroom_id": classroom_id}
        if seat_id is not None:
            query["seat_id"] = seat_id
        if from_time is not None or to_time is not None:
            time_query: MongoDocument = {}
            if from_time is not None:
                time_query["$gte"] = from_time
            if to_time is not None:
                time_query["$lt"] = to_time
            query["observed_at"] = time_query
        try:
            total = self._history.count_documents(query)
            documents = list(
                self._history.find(query)
                .sort([("observed_at", DESCENDING), ("_id", DESCENDING)])
                .skip(offset)
                .limit(limit)
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return SeatOccupancyHistoryPage(
            items=[self._history_to_domain(item) for item in documents], total=total
        )

    def create_alert(self, alert: AfterHoursAlert) -> tuple[AfterHoursAlert, bool]:
        try:
            self._alerts.insert_one(self._alert_to_document(alert))
            return alert, True
        except DuplicateKeyError:
            existing = self._find_alert({"dedupe_key": alert.dedupe_key})
            if existing is not None:
                return existing, False
            raise RepositoryUnavailableError() from None
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def get_alert(self, alert_id: str) -> AfterHoursAlert | None:
        return self._find_alert({"_id": alert_id})

    def get_alert_by_operation_id(self, operation_id: str) -> AfterHoursAlert | None:
        return self._find_alert({"operation_ids": operation_id})

    def _find_alert(self, query: MongoDocument) -> AfterHoursAlert | None:
        try:
            document = self._alerts.find_one(query)
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else self._alert_to_domain(document)

    def list_alerts(
        self,
        *,
        status: AfterHoursAlertStatus | None,
        classroom_id: str | None,
        business_date: date | None,
        limit: int,
        offset: int,
    ) -> AfterHoursAlertPage:
        query: MongoDocument = {}
        if status is not None:
            query["status"] = status.value
        if classroom_id is not None:
            query["classroom_id"] = classroom_id
        if business_date is not None:
            query["business_date"] = business_date.isoformat()
        try:
            total = self._alerts.count_documents(query)
            documents = list(
                self._alerts.find(query)
                .sort([("status", ASCENDING), ("detected_at", DESCENDING), ("_id", ASCENDING)])
                .skip(offset)
                .limit(limit)
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return AfterHoursAlertPage(
            items=[self._alert_to_domain(item) for item in documents], total=total
        )

    def replace_alert(
        self, alert: AfterHoursAlert, *, expected_version: int
    ) -> AfterHoursAlert | None:
        try:
            document = self._alerts.find_one_and_replace(
                {"_id": alert.id, "version": expected_version},
                self._alert_to_document(alert),
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            raise SeatBatchConflictError() from None
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        if document is None:
            owner = self.get_alert_by_operation_id(alert.last_operation_id)
            return owner if owner and owner.id == alert.id else None
        return self._alert_to_domain(document)

    @staticmethod
    def _classroom_to_document(item: Classroom) -> MongoDocument:
        return {
            "_id": item.id,
            "code": item.code,
            "name": item.name,
            "location": item.location,
            "timezone": item.timezone,
            "schedules": [
                {
                    "day_of_week": value.day_of_week,
                    "opens_at": value.opens_at.isoformat(),
                    "closes_at": value.closes_at.isoformat(),
                }
                for value in item.schedules
            ],
            "after_hours_grace_minutes": item.after_hours_grace_minutes,
            "is_active": item.is_active,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
            "version": item.version,
            "created_operation_id": item.created_operation_id,
            "last_operation_id": item.last_operation_id,
            "operation_ids": list(item.operation_ids),
            "responsible_staff_user_ids": list(item.responsible_staff_user_ids),
        }

    @staticmethod
    def _classroom_to_domain(document: MongoDocument) -> Classroom:
        try:
            schedules_value = document["schedules"]
            if not isinstance(schedules_value, list):
                raise TypeError
            schedules = tuple(
                ClassroomSchedule(
                    day_of_week=_integer(value, "day_of_week"),
                    opens_at=time.fromisoformat(_string(value, "opens_at")),
                    closes_at=time.fromisoformat(_string(value, "closes_at")),
                )
                for value in schedules_value
                if isinstance(value, dict)
            )
            if len(schedules) != len(schedules_value):
                raise TypeError
            return Classroom(
                id=_string(document, "_id"),
                code=_string(document, "code"),
                name=_string(document, "name"),
                location=_string(document, "location"),
                timezone=_string(document, "timezone"),
                schedules=schedules,
                after_hours_grace_minutes=_integer(document, "after_hours_grace_minutes"),
                is_active=_boolean(document, "is_active"),
                created_at=_aware_datetime(document, "created_at"),
                updated_at=_aware_datetime(document, "updated_at"),
                version=_integer(document, "version"),
                created_operation_id=_string(document, "created_operation_id"),
                last_operation_id=_string(document, "last_operation_id"),
                operation_ids=_string_tuple(document, "operation_ids"),
                responsible_staff_user_ids=_optional_string_tuple(
                    document, "responsible_staff_user_ids"
                ),
            )
        except (KeyError, TypeError, ValueError):
            raise RepositoryDataError() from None

    @staticmethod
    def _seat_to_document(item: Seat) -> MongoDocument:
        return {
            "_id": item.id,
            "classroom_id": item.classroom_id,
            "code": item.code,
            "label": item.label,
            "geometry": (
                None
                if item.geometry is None
                else {
                    "x": item.geometry.x,
                    "y": item.geometry.y,
                    "width": item.geometry.width,
                    "height": item.geometry.height,
                }
            ),
            "is_active": item.is_active,
            "current_occupancy": {
                "state": item.current_occupancy.state.value,
                "source": item.current_occupancy.source.value,
                "confidence": item.current_occupancy.confidence,
                "observed_at": item.current_occupancy.observed_at,
                "event_id": item.current_occupancy.event_id,
            },
            "created_at": item.created_at,
            "updated_at": item.updated_at,
            "version": item.version,
            "created_operation_id": item.created_operation_id,
            "last_operation_id": item.last_operation_id,
            "operation_ids": list(item.operation_ids),
        }

    @staticmethod
    def _seat_to_domain(document: MongoDocument) -> Seat:
        try:
            geometry_value = document.get("geometry")
            geometry = None
            if geometry_value is not None:
                if not isinstance(geometry_value, dict):
                    raise TypeError
                geometry = SeatGeometry(
                    x=_number(geometry_value, "x"),
                    y=_number(geometry_value, "y"),
                    width=_number(geometry_value, "width"),
                    height=_number(geometry_value, "height"),
                )
            current = document["current_occupancy"]
            if not isinstance(current, dict):
                raise TypeError
            return Seat(
                id=_string(document, "_id"),
                classroom_id=_string(document, "classroom_id"),
                code=_string(document, "code"),
                label=_string(document, "label"),
                geometry=geometry,
                is_active=_boolean(document, "is_active"),
                current_occupancy=SeatCurrentOccupancy(
                    state=SeatOccupancy(_string(current, "state")),
                    source=OccupancySource(_string(current, "source")),
                    confidence=_optional_number(current, "confidence"),
                    observed_at=_optional_aware_datetime(current, "observed_at"),
                    event_id=_optional_string(current, "event_id"),
                ),
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
    def _batch_to_document(item: SeatObservationBatchRecord) -> MongoDocument:
        return {
            "_id": item.event_id,
            "classroom_id": item.classroom_id,
            "actor_user_id": item.actor_user_id,
            "observed_at": item.observed_at,
            "observations": [
                {
                    "seat_id": value.seat_id,
                    "occupied": value.occupied,
                    "confidence": value.confidence,
                }
                for value in item.observations
            ],
            "status": item.status.value,
            "processed_count": item.processed_count,
            "changed_count": item.changed_count,
            "alert_count": item.alert_count,
            "received_at": item.received_at,
            "completed_at": item.completed_at,
        }

    @staticmethod
    def _batch_to_domain(document: MongoDocument) -> SeatObservationBatchRecord:
        try:
            values = document["observations"]
            if not isinstance(values, list):
                raise TypeError
            observations = tuple(
                SeatObservation(
                    seat_id=_string(value, "seat_id"),
                    occupied=_boolean(value, "occupied"),
                    confidence=_number(value, "confidence"),
                )
                for value in values
                if isinstance(value, dict)
            )
            if len(observations) != len(values):
                raise TypeError
            return SeatObservationBatchRecord(
                event_id=_string(document, "_id"),
                classroom_id=_string(document, "classroom_id"),
                actor_user_id=_string(document, "actor_user_id"),
                observed_at=_aware_datetime(document, "observed_at"),
                observations=observations,
                status=ObservationBatchStatus(_string(document, "status")),
                processed_count=_integer(document, "processed_count"),
                changed_count=_integer(document, "changed_count"),
                alert_count=_integer(document, "alert_count"),
                received_at=_aware_datetime(document, "received_at"),
                completed_at=_optional_aware_datetime(document, "completed_at"),
            )
        except (KeyError, TypeError, ValueError):
            raise RepositoryDataError() from None

    @staticmethod
    def _history_to_document(item: SeatOccupancyHistory) -> MongoDocument:
        return {
            "_id": item.id,
            "seat_id": item.seat_id,
            "classroom_id": item.classroom_id,
            "event_id": item.event_id,
            "from_state": item.from_state.value,
            "to_state": item.to_state.value,
            "occupied": item.occupied,
            "confidence": item.confidence,
            "observed_at": item.observed_at,
            "received_at": item.received_at,
            "applied_to_current": item.applied_to_current,
            "state_changed": item.state_changed,
        }

    @staticmethod
    def _history_to_domain(document: MongoDocument) -> SeatOccupancyHistory:
        try:
            return SeatOccupancyHistory(
                id=_string(document, "_id"),
                seat_id=_string(document, "seat_id"),
                classroom_id=_string(document, "classroom_id"),
                event_id=_string(document, "event_id"),
                from_state=SeatOccupancy(_string(document, "from_state")),
                to_state=SeatOccupancy(_string(document, "to_state")),
                occupied=_boolean(document, "occupied"),
                confidence=_number(document, "confidence"),
                observed_at=_aware_datetime(document, "observed_at"),
                received_at=_aware_datetime(document, "received_at"),
                applied_to_current=_boolean(document, "applied_to_current"),
                state_changed=_boolean(document, "state_changed"),
            )
        except (KeyError, TypeError, ValueError):
            raise RepositoryDataError() from None

    @staticmethod
    def _alert_to_document(item: AfterHoursAlert) -> MongoDocument:
        return {
            "_id": item.id,
            "dedupe_key": item.dedupe_key,
            "classroom_id": item.classroom_id,
            "seat_id": item.seat_id,
            "business_date": item.business_date.isoformat(),
            "status": item.status.value,
            "detected_at": item.detected_at,
            "resolved_at": item.resolved_at,
            "resolved_by_user_id": item.resolved_by_user_id,
            "created_operation_id": item.created_operation_id,
            "last_operation_id": item.last_operation_id,
            "operation_ids": list(item.operation_ids),
            "version": item.version,
        }

    @staticmethod
    def _alert_to_domain(document: MongoDocument) -> AfterHoursAlert:
        try:
            return AfterHoursAlert(
                id=_string(document, "_id"),
                dedupe_key=_string(document, "dedupe_key"),
                classroom_id=_string(document, "classroom_id"),
                seat_id=_string(document, "seat_id"),
                business_date=date.fromisoformat(_string(document, "business_date")),
                status=AfterHoursAlertStatus(_string(document, "status")),
                detected_at=_aware_datetime(document, "detected_at"),
                resolved_at=_optional_aware_datetime(document, "resolved_at"),
                resolved_by_user_id=_optional_string(document, "resolved_by_user_id"),
                created_operation_id=_string(document, "created_operation_id"),
                last_operation_id=_string(document, "last_operation_id"),
                operation_ids=_string_tuple(document, "operation_ids"),
                version=_integer(document, "version"),
            )
        except (KeyError, TypeError, ValueError):
            raise RepositoryDataError() from None


def _same_batch(left: SeatObservationBatchRecord, right: SeatObservationBatchRecord) -> bool:
    return (
        left.event_id == right.event_id
        and left.classroom_id == right.classroom_id
        and left.observed_at == right.observed_at
        and left.observations == right.observations
    )


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


def _integer(document: MongoDocument, field: str) -> int:
    value = document[field]
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise TypeError
    return value


def _boolean(document: MongoDocument, field: str) -> bool:
    value = document[field]
    if not isinstance(value, bool):
        raise TypeError
    return value


def _number(document: MongoDocument, field: str) -> float:
    value = document[field]
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(value):
        raise TypeError
    return float(value)


def _optional_number(document: MongoDocument, field: str) -> float | None:
    value = document.get(field)
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(value):
        raise TypeError
    return float(value)


def _aware_datetime(document: MongoDocument, field: str) -> datetime:
    value = document[field]
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise TypeError
    return value


def _optional_aware_datetime(document: MongoDocument, field: str) -> datetime | None:
    value = document.get(field)
    if value is not None and (not isinstance(value, datetime) or value.tzinfo is None):
        raise TypeError
    return value


def _string_tuple(document: MongoDocument, field: str) -> tuple[str, ...]:
    value = document[field]
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise TypeError
    return tuple(value)


def _optional_string_tuple(document: MongoDocument, field: str) -> tuple[str, ...]:
    value = document.get(field, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise TypeError
    return tuple(value)
