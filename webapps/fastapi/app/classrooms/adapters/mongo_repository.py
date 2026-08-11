"""강의실·좌석·관측 metadata의 PyMongo adapter."""

from __future__ import annotations

from datetime import datetime
from math import isfinite

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from ...shared.database import MongoDatabase, MongoDocument
from ...shared.errors import RepositoryDataError, RepositoryUnavailableError
from ..errors import ClassroomDuplicateError, SeatBatchConflictError, SeatDuplicateError
from ..models import (
    Classroom,
    ClassroomPage,
    ObservationBatchStatus,
    OccupancySource,
    Seat,
    SeatCurrentOccupancy,
    SeatGeometry,
    SeatObservation,
    SeatObservationBatchRecord,
    SeatOccupancy,
    SeatOccupancyHistory,
    SeatPage,
)


class MongoClassroomRepository:
    classroom_collection_name = "classrooms"
    seat_collection_name = "seats"
    batch_collection_name = "seat_observation_batches"
    history_collection_name = "seat_occupancy_history"

    def __init__(self, database: MongoDatabase) -> None:
        self._classrooms = database[self.classroom_collection_name]
        self._seats = database[self.seat_collection_name]
        self._batches = database[self.batch_collection_name]
        self._history = database[self.history_collection_name]

    @classmethod
    def ensure_indexes(cls, database: MongoDatabase) -> None:
        database[cls.classroom_collection_name].create_index(
            [("code", ASCENDING)], name="classrooms_code_unique", unique=True
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

    def create_classroom(self, classroom: Classroom) -> Classroom:
        try:
            self._classrooms.insert_one(self._classroom_to_document(classroom))
            return classroom
        except DuplicateKeyError:
            existing = self.get_classroom(classroom.id)
            if existing == classroom:
                return existing
            raise ClassroomDuplicateError() from None
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def get_classroom(self, classroom_id: str) -> Classroom | None:
        return self._find_classroom({"_id": classroom_id})

    def get_classroom_by_code(self, code: str) -> Classroom | None:
        return self._find_classroom({"code": code})

    def _find_classroom(self, query: MongoDocument) -> Classroom | None:
        try:
            document = self._classrooms.find_one(query)
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else self._classroom_to_domain(document)

    def list_classrooms(self, *, limit: int, offset: int) -> ClassroomPage:
        query: MongoDocument = {"is_active": True}
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

    def create_seat(self, seat: Seat) -> Seat:
        try:
            self._seats.insert_one(self._seat_to_document(seat))
            return seat
        except DuplicateKeyError:
            existing = self.get_seat(seat.id)
            if existing == seat:
                return existing
            raise SeatDuplicateError() from None
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def get_seat(self, seat_id: str) -> Seat | None:
        try:
            document = self._seats.find_one({"_id": seat_id})
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else self._seat_to_domain(document)

    def list_seats(self, classroom_id: str, *, limit: int, offset: int) -> SeatPage:
        query: MongoDocument = {"classroom_id": classroom_id, "is_active": True}
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
        update_fields = self._seat_to_document(seat)
        update_fields.pop("_id")
        try:
            document = self._seats.find_one_and_update(
                {"_id": seat.id, "version": expected_version},
                {"$set": update_fields},
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else self._seat_to_domain(document)

    def claim_observation_batch(
        self, record: SeatObservationBatchRecord
    ) -> SeatObservationBatchRecord:
        try:
            self._batches.insert_one(self._batch_to_document(record))
            return record
        except DuplicateKeyError:
            existing = self.get_observation_batch(record.event_id)
            if existing is None or not _same_batch(existing, record):
                raise SeatBatchConflictError() from None
            return existing
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
        update_fields = self._batch_to_document(record)
        update_fields.pop("_id")
        try:
            document = self._batches.find_one_and_update(
                {"_id": record.event_id, "status": ObservationBatchStatus.PROCESSING.value},
                {"$set": update_fields},
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        if document is not None:
            return self._batch_to_domain(document)
        existing = self.get_observation_batch(record.event_id)
        if (
            existing is not None
            and existing.status == ObservationBatchStatus.COMPLETED
            and _same_batch(existing, record)
        ):
            return existing
        raise SeatBatchConflictError()

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
                return history
            raise SeatBatchConflictError() from None
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    @staticmethod
    def _classroom_to_document(item: Classroom) -> MongoDocument:
        return {
            "_id": item.id,
            "code": item.code,
            "name": item.name,
            "location": item.location,
            "is_active": item.is_active,
            "created_at": item.created_at,
        }

    @staticmethod
    def _classroom_to_domain(document: MongoDocument) -> Classroom:
        try:
            return Classroom(
                id=_required_str(document, "_id"),
                code=_required_str(document, "code"),
                name=_required_str(document, "name"),
                location=_required_str(document, "location"),
                is_active=_required_bool(document, "is_active"),
                created_at=_required_datetime(document, "created_at"),
            )
        except (KeyError, TypeError, ValueError):
            raise RepositoryDataError() from None

    @staticmethod
    def _seat_to_document(item: Seat) -> MongoDocument:
        geometry = item.geometry
        return {
            "_id": item.id,
            "classroom_id": item.classroom_id,
            "code": item.code,
            "label": item.label,
            "geometry": (
                None
                if geometry is None
                else {
                    "x": geometry.x,
                    "y": geometry.y,
                    "width": geometry.width,
                    "height": geometry.height,
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
        }

    @staticmethod
    def _seat_to_domain(document: MongoDocument) -> Seat:
        try:
            geometry_document = document.get("geometry")
            geometry = None
            if geometry_document is not None:
                if not isinstance(geometry_document, dict):
                    raise TypeError
                geometry = SeatGeometry(
                    x=_finite_float(geometry_document, "x"),
                    y=_finite_float(geometry_document, "y"),
                    width=_finite_float(geometry_document, "width"),
                    height=_finite_float(geometry_document, "height"),
                )
            occupancy = _required_document(document, "current_occupancy")
            confidence_value = occupancy.get("confidence")
            confidence = None if confidence_value is None else float(confidence_value)
            if confidence is not None and not isfinite(confidence):
                raise ValueError
            return Seat(
                id=_required_str(document, "_id"),
                classroom_id=_required_str(document, "classroom_id"),
                code=_required_str(document, "code"),
                label=_required_str(document, "label"),
                geometry=geometry,
                is_active=_required_bool(document, "is_active"),
                current_occupancy=SeatCurrentOccupancy(
                    state=SeatOccupancy(_required_str(occupancy, "state")),
                    source=OccupancySource(_required_str(occupancy, "source")),
                    confidence=confidence,
                    observed_at=_optional_datetime(occupancy, "observed_at"),
                    event_id=_optional_str(occupancy, "event_id"),
                ),
                created_at=_required_datetime(document, "created_at"),
                updated_at=_required_datetime(document, "updated_at"),
                version=_required_int(document, "version"),
            )
        except (KeyError, TypeError, ValueError):
            raise RepositoryDataError() from None

    @staticmethod
    def _batch_to_document(item: SeatObservationBatchRecord) -> MongoDocument:
        return {
            "_id": item.event_id,
            "classroom_id": item.classroom_id,
            "source": item.source.value,
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
            "received_at": item.received_at,
            "completed_at": item.completed_at,
        }

    @staticmethod
    def _batch_to_domain(document: MongoDocument) -> SeatObservationBatchRecord:
        try:
            observations_value = document["observations"]
            if not isinstance(observations_value, list):
                raise TypeError
            observations = tuple(
                SeatObservation(
                    seat_id=_required_str(value, "seat_id"),
                    occupied=_required_bool(value, "occupied"),
                    confidence=_finite_float(value, "confidence"),
                )
                for value in observations_value
                if isinstance(value, dict)
            )
            if len(observations) != len(observations_value):
                raise TypeError
            return SeatObservationBatchRecord(
                event_id=_required_str(document, "_id"),
                classroom_id=_required_str(document, "classroom_id"),
                source=OccupancySource(str(document.get("source", OccupancySource.MOCK.value))),
                observed_at=_required_datetime(document, "observed_at"),
                observations=observations,
                status=ObservationBatchStatus(_required_str(document, "status")),
                processed_count=_required_int(document, "processed_count"),
                changed_count=_required_int(document, "changed_count"),
                received_at=_required_datetime(document, "received_at"),
                completed_at=_optional_datetime(document, "completed_at"),
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
            "source": item.source.value,
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
                id=_required_str(document, "_id"),
                seat_id=_required_str(document, "seat_id"),
                classroom_id=_required_str(document, "classroom_id"),
                event_id=_required_str(document, "event_id"),
                source=OccupancySource(str(document.get("source", OccupancySource.MOCK.value))),
                from_state=SeatOccupancy(_required_str(document, "from_state")),
                to_state=SeatOccupancy(_required_str(document, "to_state")),
                occupied=_required_bool(document, "occupied"),
                confidence=_finite_float(document, "confidence"),
                observed_at=_required_datetime(document, "observed_at"),
                received_at=_required_datetime(document, "received_at"),
                applied_to_current=_required_bool(document, "applied_to_current"),
                state_changed=_required_bool(document, "state_changed"),
            )
        except (KeyError, TypeError, ValueError):
            raise RepositoryDataError() from None


def _same_batch(left: SeatObservationBatchRecord, right: SeatObservationBatchRecord) -> bool:
    return (
        left.event_id == right.event_id
        and left.classroom_id == right.classroom_id
        and left.source == right.source
        and left.observed_at == right.observed_at
        and left.observations == right.observations
    )


def _required_document(document: MongoDocument, key: str) -> MongoDocument:
    value = document[key]
    if not isinstance(value, dict):
        raise TypeError
    return value


def _required_str(document: MongoDocument, key: str) -> str:
    value = document[key]
    if not isinstance(value, str) or not value:
        raise TypeError
    return value


def _optional_str(document: MongoDocument, key: str) -> str | None:
    value = document.get(key)
    if value is not None and not isinstance(value, str):
        raise TypeError
    return value


def _required_bool(document: MongoDocument, key: str) -> bool:
    value = document[key]
    if not isinstance(value, bool):
        raise TypeError
    return value


def _required_int(document: MongoDocument, key: str) -> int:
    value = document[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError
    return value


def _required_datetime(document: MongoDocument, key: str) -> datetime:
    value = document[key]
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise TypeError
    return value


def _optional_datetime(document: MongoDocument, key: str) -> datetime | None:
    value = document.get(key)
    if value is not None and (not isinstance(value, datetime) or value.tzinfo is None):
        raise TypeError
    return value


def _finite_float(document: MongoDocument, key: str) -> float:
    value = float(document[key])
    if not isfinite(value):
        raise ValueError
    return value
