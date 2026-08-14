"""강의실·좌석·관측 metadata의 PyMongo adapter."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from math import isfinite
from typing import cast

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.client_session import ClientSession
from pymongo.errors import (
    AutoReconnect,
    DuplicateKeyError,
    NotPrimaryError,
    OperationFailure,
    PyMongoError,
)

from ...shared.database import DatabaseOperationError, MongoDatabase, MongoDocument
from ...shared.errors import RepositoryDataError, RepositoryUnavailableError
from ..errors import (
    ClassroomDuplicateError,
    ClassroomNotFoundError,
    SeatBatchConflictError,
    SeatDuplicateError,
    SeatInactiveForAssignmentError,
    SeatNotFoundError,
)
from ..models import (
    Classroom,
    ClassroomPage,
    ObservationBatchStatus,
    OccupancySource,
    RepairApproval,
    RepairApprovalStatus,
    Seat,
    SeatAssignment,
    SeatCurrentOccupancy,
    SeatGeometry,
    SeatMigrationAction,
    SeatMigrationRecord,
    SeatMigrationSnapshot,
    SeatMigrationSnapshotPayload,
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
        database[cls.seat_collection_name].create_index(
            [("classroom_id", ASCENDING), ("row", ASCENDING), ("column", ASCENDING)],
            name="seats_classroom_row_column_unique",
            unique=True,
            partialFilterExpression={
                "row": {"$type": "number"},
                "column": {"$type": "number"},
            },
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

    @classmethod
    def migrate_seat_row_column(
        cls,
        database: MongoDatabase,
        *,
        columns_per_row: int = 4,
    ) -> int:
        """기존 좌석의 행·열을 코드 순서로 초기화합니다.

        row/column이 없는(또는 None인) 기존 좌석만 대상으로, 코드 오름차순으로
        행당 ``columns_per_row`` 개씩 행·열을 할당합니다. 이미 행·열이 있는 좌석은
        건드리지 않으므로 재실행해도 멱등입니다.

        Args:
            database: MongoDB 데이터베이스
            columns_per_row: 행당 열 수 (기본값 4)

        Returns:
            업데이트된 좌석 수
        """
        classrooms = database[cls.classroom_collection_name].find({"is_active": True})
        updated_count = 0

        for classroom in classrooms:
            seats = list(
                database[cls.seat_collection_name]
                .find(
                    {"classroom_id": classroom["_id"], "is_active": True},
                )
                .sort("code", ASCENDING)
            )

            for idx, seat in enumerate(seats):
                if seat.get("row") is None or seat.get("column") is None:
                    # 코드 순서로 행·열 할당
                    row = (idx // columns_per_row) + 1
                    column = (idx % columns_per_row) + 1
                    database[cls.seat_collection_name].update_one(
                        {"_id": seat["_id"]},
                        {"$set": {"row": row, "column": column}},
                    )
                    updated_count += 1

        return updated_count

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

    def update_classroom(self, classroom: Classroom) -> Classroom:
        update_fields = self._classroom_to_document(classroom)
        update_fields.pop("_id")
        try:
            document = self._classrooms.find_one_and_update(
                {"_id": classroom.id},
                {"$set": update_fields},
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            raise ClassroomDuplicateError() from None
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        if document is None:
            raise ClassroomNotFoundError()
        return self._classroom_to_domain(document)

    def delete_classroom(self, classroom_id: str) -> None:
        try:
            document = self._classrooms.find_one_and_update(
                {"_id": classroom_id},
                {"$set": {"is_active": False}},
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        if document is None:
            raise ClassroomNotFoundError()

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

    def list_all_seats_for_allocation(self, classroom_id: str) -> list[Seat]:
        """allocator용: 비활성(legacy inactive) 포함 모든 좌석을 돌려준다.

        unique index가 is_active와 무관하게 적용되므로 비활성 좌석도 코드·좌표를
        예약한다. allocator는 이 메서드가 돌려준 현재 좌석 문서만 보고 코드를 생성한다.
        """
        query: MongoDocument = {"classroom_id": classroom_id}
        try:
            documents = list(
                self._seats.find(query).sort([("code", ASCENDING), ("_id", ASCENDING)])
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return [self._seat_to_domain(item) for item in documents]

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

    def update_seat(self, seat: Seat, *, unset_fields: list[str] | None = None) -> Seat:
        # 도메인 객체 전체를 $set으로 반영하고, unset_fields가 있으면
        # 해당 필드는 $unset으로 제거한다 (행·열 해제 시 사용).
        update_fields = self._seat_to_document(seat)
        update_fields.pop("_id")
        update: MongoDocument = {"$set": update_fields}
        if unset_fields:
            update["$unset"] = dict.fromkeys(unset_fields, "")
        try:
            document = self._seats.find_one_and_update(
                {"_id": seat.id},
                update,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            raise SeatDuplicateError() from None
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        if document is None:
            raise SeatNotFoundError()
        return self._seat_to_domain(document)

    def delete_seat(self, seat_id: str) -> None:
        """좌석 문서를 물리적으로 삭제한다 (hard delete).

        ``is_active=False``를 기록하지 않고 문서를 지우므로 code와
        (row, column) unique key가 즉시 해제된다. history·batch 컬렉션은
        cascade 삭제하지 않는다.
        """
        try:
            result = self._seats.delete_one({"_id": seat_id})
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        if result.deleted_count == 0:
            raise SeatNotFoundError()

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
        document: MongoDocument = {
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
            "current_occupancy": _occupancy_to_document(item.current_occupancy),
            "created_at": item.created_at,
            "updated_at": item.updated_at,
            "version": item.version,
        }
        # row/column이 None이면 문서에서 생략한다. partial 인덱스가 number 타입만
        # 대상으로 하므로 None을 명시적으로 저장하면 unique 인덱스에 걸리지 않지만,
        # 문서에 남은 None 필드는 하위 호환 혼란을 줄 수 있어 저장 자체를 하지 않는다.
        if item.row is not None:
            document["row"] = item.row
        if item.column is not None:
            document["column"] = item.column
        return document

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
                row=_optional_int(document, "row"),
                column=_optional_int(document, "column"),
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


class MongoSeatMutationUnitOfWork:
    """Mongo transaction 기반 좌석 mutation UoW.

    `MongoClassroomRepository`와 같은 database(client)를 공유해 seats,
    seat_assignments, seat_occupancy_history 컬렉션을 원자적으로 mutation한다.
    관측 적용도 같은 transaction 계열을 쓰므로 hard delete와 좌석 단위로
    선형화된다. replica set(mongos 포함)이 아니면 transaction을 지원하지 않으므로
    생성 시 topology를 검사해 ``DatabaseOperationError``로 startup fail한다.

    transient 오류는 최대 3회 재시도하고, commit 결과가 불확실하면 재조회로
    최종 상태가 확정됐는지 확인한다. 확인되지 않으면 ``RepositoryUnavailableError``
    (503)로 변환한다.
    """

    assignment_collection_name = "seat_assignments"
    _TRANSIENT_RETRY_LIMIT = 3

    def __init__(
        self,
        repository: MongoClassroomRepository,
        database: MongoDatabase,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._database = database
        # repository와 같은 seats·history collection 객체를 공유해 mutation이
        # repository 조회에 그대로 보이게 한다.
        self._seats = repository._seats
        self._history = repository._history
        self._assignments = database[self.assignment_collection_name]
        self._clock = clock or (lambda: datetime.now(UTC))
        self._check_transaction_topology()

    @classmethod
    def ensure_indexes(cls, database: MongoDatabase) -> None:
        """지정 저장소의 unique index(seat_id, (classroom_id, student_id))를 만든다."""
        database[cls.assignment_collection_name].create_index(
            [("seat_id", ASCENDING)], name="seat_assignments_seat_id_unique", unique=True
        )
        database[cls.assignment_collection_name].create_index(
            [("classroom_id", ASCENDING), ("student_id", ASCENDING)],
            name="seat_assignments_classroom_student_unique",
            unique=True,
        )

    def _check_transaction_topology(self) -> None:
        """transaction에 필요한 replica set(또는 mongos) 여부를 확인한다.

        지원하지 않는 topology면 즉시 ``DatabaseOperationError``를 던진다.
        """
        try:
            response = self._database.command("hello")
        except PyMongoError:
            raise DatabaseOperationError(
                "MongoDB transaction topology를 확인할 수 없습니다."
            ) from None
        if not isinstance(response, dict):
            raise DatabaseOperationError("MongoDB transaction topology를 확인할 수 없습니다.")
        is_replica_set = response.get("setName") is not None
        is_mongos = response.get("msg") == "isdbgrid"
        if not is_replica_set and not is_mongos:
            raise DatabaseOperationError(
                "MongoDB가 replica set을 지원하지 않아 transaction을 사용할 수 없습니다."
            )

    def assign_or_move_if_active(
        self, seat_id: str, student_id: str, classroom_id: str
    ) -> SeatAssignment:
        """학생을 좌석에 지정한다.

        같은 강의실의 다른 좌석에 이미 지정된 학생이면 기존 지정을 해제하고
        새 좌석에 지정한다. (classroom_id, student_id) unique index가 한 학생의
        중복 지정을 막는다. 좌석이 없거나 비활성이면 memory와 동일하게
        ``SeatNotFoundError``·``SeatInactiveForAssignmentError``를 던진다.
        """

        def perform(session: ClientSession) -> SeatAssignment:
            seat = self._seats.find_one({"_id": seat_id}, session=session)
            if seat is None:
                raise SeatNotFoundError()
            if seat.get("is_active") is not True:
                raise SeatInactiveForAssignmentError()
            existing = self._assignments.find_one(
                {"classroom_id": classroom_id, "student_id": student_id},
                session=session,
            )
            if existing is not None and existing.get("seat_id") != seat_id:
                self._assignments.delete_one({"_id": existing["_id"]}, session=session)
            assignment = SeatAssignment(
                seat_id=seat_id,
                student_id=student_id,
                classroom_id=classroom_id,
                assigned_at=self._clock(),
            )
            self._assignments.replace_one(
                {"seat_id": seat_id},
                _assignment_to_document(assignment),
                upsert=True,
                session=session,
            )
            return assignment

        def verify_committed() -> bool:
            document = self._assignments.find_one({"seat_id": seat_id})
            if document is None:
                return False
            return (
                document.get("student_id") == student_id
                and document.get("classroom_id") == classroom_id
            )

        result = self._commit_with_retry(perform, verify_committed)
        return cast(SeatAssignment, result)

    def delete_seat_and_unassign(self, seat_id: str) -> None:
        """좌석 문서와 현재 지정을 같은 transaction에서 물리 삭제한다 (hard delete).

        ``is_active``에 false를 기록하지 않고 문서를 제거하므로 code와
        (row, column) unique key가 즉시 해제된다. ``seat_occupancy_history``와
        ``seat_observation_batches``는 손대지 않는다 (cascade 없음).
        좌석 문서가 없으면 ``SeatNotFoundError``를 던지고 transaction은 abort된다.

        commit 결과가 불확실하면 seat·assignment 양쪽을 재조회해 부재(absence)를
        확인하고, 확인되지 않으면 재시도 후 503으로 변환한다.
        """

        def perform(session: ClientSession) -> None:
            result = self._seats.delete_one({"_id": seat_id}, session=session)
            if result.deleted_count == 0:
                raise SeatNotFoundError()
            self._assignments.delete_one({"seat_id": seat_id}, session=session)

        def verify_committed() -> bool:
            seat_absent = self._seats.find_one({"_id": seat_id}) is None
            assignment_absent = self._assignments.find_one({"seat_id": seat_id}) is None
            return seat_absent and assignment_absent

        self._commit_with_retry(perform, verify_committed)

    def unassign_if_active(self, seat_id: str) -> None:
        """활성 좌석의 지정만 해제한다. 비활성·미존재 좌석은 무시한다."""

        def perform(session: ClientSession) -> None:
            seat = self._seats.find_one({"_id": seat_id}, session=session)
            if seat is None or seat.get("is_active") is not True:
                return
            self._assignments.delete_one({"seat_id": seat_id}, session=session)

        def verify_committed() -> bool:
            seat = self._seats.find_one({"_id": seat_id})
            if seat is None or seat.get("is_active") is not True:
                return True
            return self._assignments.find_one({"seat_id": seat_id}) is None

        self._commit_with_retry(perform, verify_committed)

    def append_history_and_apply_occupancy(
        self,
        history: SeatOccupancyHistory,
        *,
        expected_version: int,
        occupancy: SeatCurrentOccupancy | None,
        updated_at: datetime,
    ) -> SeatOccupancyHistory | None:
        """관측 history 기록과 current 반영을 한 transaction에서 수행한다.

        hard delete와 같은 session 계열을 쓰므로 좌석 단위로 선형화된다.
        좌석 문서가 없거나 비활성이면 transaction을 abort해 아무것도 쓰지 않고
        ``SeatNotFoundError``를 던진다 (동시 delete가 이긴다). version이
        어긋나면 ``None``을 돌려준다.
        """
        history_query: MongoDocument = {
            "event_id": history.event_id,
            "seat_id": history.seat_id,
        }

        def perform(session: ClientSession) -> SeatOccupancyHistory | None:
            seat = self._seats.find_one({"_id": history.seat_id}, session=session)
            if seat is None or seat.get("is_active") is not True:
                raise SeatNotFoundError()
            if seat.get("version") != expected_version:
                return None
            existing = self._history.find_one(history_query, session=session)
            if existing is not None:
                stored = MongoClassroomRepository._history_to_domain(existing)
                if stored != history:
                    raise SeatBatchConflictError()
            else:
                self._history.insert_one(
                    MongoClassroomRepository._history_to_document(history), session=session
                )
                stored = history
            if occupancy is not None:
                self._seats.update_one(
                    {"_id": history.seat_id, "version": expected_version},
                    {
                        "$set": {
                            "current_occupancy": _occupancy_to_document(occupancy),
                            "updated_at": updated_at,
                            "version": expected_version + 1,
                        }
                    },
                    session=session,
                )
            return stored

        def verify_committed() -> bool:
            if self._history.find_one(history_query) is None:
                return False
            if occupancy is None:
                return True
            seat = self._seats.find_one({"_id": history.seat_id})
            return seat is not None and seat.get("version") == expected_version + 1

        result = self._commit_with_retry(perform, verify_committed)
        return cast(SeatOccupancyHistory | None, result)

    def _commit_with_retry(
        self,
        perform: Callable[[ClientSession], object],
        verify_committed: Callable[[], bool],
    ) -> object:
        """transaction을 실행·commit하고 오류를 정책대로 처리한다.

        - commit 불확실(UnknownTransactionCommitResult): 재조회로 확정 확인
          - 확정 → 성공 반환
          - 미확정(rollback) → 재시도
        - transient(AutoReconnect·NotPrimary·TransientTransactionError): 재시도
        - 재시도 소진·재조회 실패·예상 밖 PyMongoError: 503
        """
        last_error: PyMongoError | None = None
        result: object = None
        for _ in range(self._TRANSIENT_RETRY_LIMIT):
            session: ClientSession | None = None
            try:
                # start_session도 try 안에서 실행해 세션 생성 실패를 retry·503 정책에
                # 포함시킨다.
                session = self._database.client.start_session()
                with session.start_transaction():
                    result = perform(session)
                    session.commit_transaction()
                return result
            except PyMongoError as exc:
                # commit 시점 오류는 transient보다 commit 불확실을 먼저 검사한다.
                # AutoReconnect·NotPrimaryError도 commit 시점이면
                # UnknownTransactionCommitResult label이 붙을 수 있어,
                # isinstance 기반 transient가 우선하면 재조회를 건너뛴다.
                if _is_ambiguous_commit_error(exc):
                    last_error = exc
                    try:
                        if verify_committed():
                            return result
                    except PyMongoError:
                        raise RepositoryUnavailableError() from exc
                    continue
                if _is_transient_transaction_error(exc):
                    last_error = exc
                    continue
                raise RepositoryUnavailableError() from exc
            finally:
                if session is not None:
                    session.end_session()
        raise RepositoryUnavailableError() from last_error


class MongoSeatAssignmentRepository:
    """좌석-학생 지정 전용 MongoDB 저장소.

    `MongoSeatMutationUnitOfWork`와 같은 database(client)를 공유해 UoW가
    transaction으로 쓴 seat_assignments 컬렉션을 읽기 경로(list_assignments
    등)에 그대로 노출한다. mutation은 UoW가 원자적으로 수행하므로 이
    저장소의 assign/unassign은 UoW 미주입 레거시 경로에서만 쓰인다.
    """

    def __init__(self, database: MongoDatabase) -> None:
        self._assignments = database[MongoSeatMutationUnitOfWork.assignment_collection_name]

    def assign(self, assignment: SeatAssignment) -> SeatAssignment:
        """학생을 좌석에 지정한다. 이미 지정되어 있으면 덮어쓴다."""
        try:
            self._assignments.replace_one(
                {"seat_id": assignment.seat_id},
                _assignment_to_document(assignment),
                upsert=True,
            )
            return assignment
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def unassign(self, seat_id: str) -> None:
        """좌석-학생 지정을 해제한다."""
        try:
            self._assignments.delete_one({"seat_id": seat_id})
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def get_by_seat(self, seat_id: str) -> SeatAssignment | None:
        """좌석 ID로 지정을 조회한다."""
        try:
            document = self._assignments.find_one({"seat_id": seat_id})
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else _assignment_to_domain(document)

    def get_by_student(self, student_id: str, classroom_id: str) -> SeatAssignment | None:
        """학생 ID와 강의실 ID로 지정을 조회한다."""
        try:
            document = self._assignments.find_one(
                {"student_id": student_id, "classroom_id": classroom_id}
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else _assignment_to_domain(document)

    def list_by_classroom(self, classroom_id: str) -> list[SeatAssignment]:
        """강의실의 모든 지정을 조회한다."""
        try:
            documents = list(self._assignments.find({"classroom_id": classroom_id}))
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return [_assignment_to_domain(document) for document in documents]

    def unassign_by_student(self, student_id: str) -> int:
        """학생의 모든 좌석 지정을 해제한다. 해제된 지정 수를 반환한다."""
        try:
            result = self._assignments.delete_many({"student_id": student_id})
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return result.deleted_count


class MongoSeatMigrationRepository:
    """오프라인 migration snapshot·record·approval의 MongoDB adapter.

    manifest 컬렉션(`seat_migration_snapshots`)에는 비민감 metadata만 저장하고,
    복원용 payload(`seat_migration_snapshot_data`)는 별도 컬렉션에 보관해
    manifest에 좌석·지정 내용이 남지 않게 한다. 감사 기록과 승인 기록은
    각각의 컬렉션에 저장한다.
    """

    snapshot_collection_name = "seat_migration_snapshots"
    snapshot_data_collection_name = "seat_migration_snapshot_data"
    record_collection_name = "seat_migration_records"
    approval_collection_name = "seat_migration_repair_approvals"

    def __init__(self, database: MongoDatabase) -> None:
        self._snapshots = database[self.snapshot_collection_name]
        self._snapshot_data = database[self.snapshot_data_collection_name]
        self._records = database[self.record_collection_name]
        self._approvals = database[self.approval_collection_name]

    @classmethod
    def ensure_indexes(cls, database: MongoDatabase) -> None:
        """migration 저장소 컬렉션의 조회용 index를 만든다."""
        database[cls.snapshot_collection_name].create_index(
            [("classroom_id", ASCENDING), ("created_at", DESCENDING)],
            name="seat_migration_snapshots_classroom_time",
        )
        database[cls.record_collection_name].create_index(
            [("classroom_id", ASCENDING), ("created_at", ASCENDING)],
            name="seat_migration_records_classroom_time",
        )
        database[cls.approval_collection_name].create_index(
            [("classroom_id", ASCENDING), ("seat_id", ASCENDING)],
            name="seat_migration_approvals_classroom_seat",
        )

    def save_snapshot(
        self,
        snapshot: SeatMigrationSnapshot,
        *,
        seats: tuple[Seat, ...],
        assignments: tuple[SeatAssignment, ...],
    ) -> SeatMigrationSnapshot:
        try:
            self._snapshots.insert_one(self._snapshot_to_document(snapshot))
            self._snapshot_data.replace_one(
                {"_id": snapshot.id},
                self._payload_to_document(snapshot.id, seats, assignments),
                upsert=True,
            )
            return snapshot
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def get_snapshot(self, snapshot_id: str) -> SeatMigrationSnapshot | None:
        try:
            document = self._snapshots.find_one({"_id": snapshot_id})
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else self._snapshot_to_domain(document)

    def get_snapshot_payload(self, snapshot_id: str) -> SeatMigrationSnapshotPayload | None:
        try:
            document = self._snapshot_data.find_one({"_id": snapshot_id})
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else self._payload_to_domain(document)

    def latest_snapshot(self, classroom_id: str) -> SeatMigrationSnapshot | None:
        try:
            document = self._snapshots.find_one(
                {"classroom_id": classroom_id},
                sort=[("created_at", DESCENDING)],
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else self._snapshot_to_domain(document)

    def mark_snapshot_restored(self, snapshot_id: str, restored_at: datetime) -> None:
        try:
            self._snapshots.update_one(
                {"_id": snapshot_id},
                {"$set": {"restored_at": restored_at}},
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def save_record(self, record: SeatMigrationRecord) -> SeatMigrationRecord:
        try:
            self._records.replace_one(
                {"_id": record.id},
                self._record_to_document(record),
                upsert=True,
            )
            return record
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def list_records(
        self, classroom_id: str, *, snapshot_id: str | None = None
    ) -> list[SeatMigrationRecord]:
        query: MongoDocument = {"classroom_id": classroom_id}
        if snapshot_id is not None:
            query["snapshot_id"] = snapshot_id
        try:
            documents = list(
                self._records.find(query).sort([("created_at", ASCENDING), ("_id", ASCENDING)])
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return [self._record_to_domain(document) for document in documents]

    def save_approval(self, approval: RepairApproval) -> RepairApproval:
        try:
            self._approvals.replace_one(
                {"_id": approval.id},
                self._approval_to_document(approval),
                upsert=True,
            )
            return approval
        except PyMongoError:
            raise RepositoryUnavailableError() from None

    def get_approval(self, approval_id: str) -> RepairApproval | None:
        try:
            document = self._approvals.find_one({"_id": approval_id})
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else self._approval_to_domain(document)

    def list_approvals(
        self, classroom_id: str, *, seat_id: str | None = None
    ) -> list[RepairApproval]:
        query: MongoDocument = {"classroom_id": classroom_id}
        if seat_id is not None:
            query["seat_id"] = seat_id
        try:
            documents = list(
                self._approvals.find(query).sort([("requested_at", ASCENDING), ("_id", ASCENDING)])
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return [self._approval_to_domain(document) for document in documents]

    def approved_approval_for_seat(self, classroom_id: str, seat_id: str) -> RepairApproval | None:
        try:
            document = self._approvals.find_one(
                {
                    "classroom_id": classroom_id,
                    "seat_id": seat_id,
                    "status": RepairApprovalStatus.APPROVED.value,
                }
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else self._approval_to_domain(document)

    @staticmethod
    def _snapshot_to_document(item: SeatMigrationSnapshot) -> MongoDocument:
        return {
            "_id": item.id,
            "classroom_id": item.classroom_id,
            "name": item.name,
            "seats_count": item.seats_count,
            "assignments_count": item.assignments_count,
            "checksum": item.checksum,
            "created_at": item.created_at,
            "expires_at": item.expires_at,
            "restored_at": item.restored_at,
        }

    @staticmethod
    def _snapshot_to_domain(document: MongoDocument) -> SeatMigrationSnapshot:
        try:
            return SeatMigrationSnapshot(
                id=_required_str(document, "_id"),
                classroom_id=_required_str(document, "classroom_id"),
                name=_required_str(document, "name"),
                seats_count=_required_int(document, "seats_count"),
                assignments_count=_required_int(document, "assignments_count"),
                checksum=_required_str(document, "checksum"),
                created_at=_required_datetime(document, "created_at"),
                expires_at=_required_datetime(document, "expires_at"),
                restored_at=_optional_datetime(document, "restored_at"),
            )
        except (KeyError, TypeError, ValueError):
            raise RepositoryDataError() from None

    @staticmethod
    def _payload_to_document(
        snapshot_id: str,
        seats: tuple[Seat, ...],
        assignments: tuple[SeatAssignment, ...],
    ) -> MongoDocument:
        return {
            "_id": snapshot_id,
            "seats": [MongoClassroomRepository._seat_to_document(seat) for seat in seats],
            "assignments": [_assignment_to_document(assignment) for assignment in assignments],
        }

    @staticmethod
    def _payload_to_domain(document: MongoDocument) -> SeatMigrationSnapshotPayload:
        try:
            seats_value = document["seats"]
            assignments_value = document["assignments"]
            if not isinstance(seats_value, list) or not isinstance(assignments_value, list):
                raise TypeError
            seats = tuple(
                MongoClassroomRepository._seat_to_domain(value)
                for value in seats_value
                if isinstance(value, dict)
            )
            assignments = tuple(
                _assignment_to_domain(value)
                for value in assignments_value
                if isinstance(value, dict)
            )
            if len(seats) != len(seats_value) or len(assignments) != len(assignments_value):
                raise TypeError
            return SeatMigrationSnapshotPayload(seats=seats, assignments=assignments)
        except (KeyError, TypeError, ValueError):
            raise RepositoryDataError() from None

    @staticmethod
    def _record_to_document(item: SeatMigrationRecord) -> MongoDocument:
        return {
            "_id": item.id,
            "classroom_id": item.classroom_id,
            "seat_id": item.seat_id,
            "action": item.action.value,
            "previous_row": item.previous_row,
            "previous_column": item.previous_column,
            "new_row": item.new_row,
            "new_column": item.new_column,
            "created_at": item.created_at,
            "snapshot_id": item.snapshot_id,
        }

    @staticmethod
    def _record_to_domain(document: MongoDocument) -> SeatMigrationRecord:
        try:
            return SeatMigrationRecord(
                id=_required_str(document, "_id"),
                classroom_id=_required_str(document, "classroom_id"),
                seat_id=_required_str(document, "seat_id"),
                action=SeatMigrationAction(_required_str(document, "action")),
                previous_row=_optional_int(document, "previous_row"),
                previous_column=_optional_int(document, "previous_column"),
                new_row=_optional_int(document, "new_row"),
                new_column=_optional_int(document, "new_column"),
                created_at=_required_datetime(document, "created_at"),
                snapshot_id=_optional_str(document, "snapshot_id"),
            )
        except (KeyError, TypeError, ValueError):
            raise RepositoryDataError() from None

    @staticmethod
    def _approval_to_document(item: RepairApproval) -> MongoDocument:
        return {
            "_id": item.id,
            "classroom_id": item.classroom_id,
            "seat_id": item.seat_id,
            "requested_row": item.requested_row,
            "requested_column": item.requested_column,
            "requested_by": item.requested_by,
            "requested_at": item.requested_at,
            "status": item.status.value,
            "approved_by": item.approved_by,
            "approved_at": item.approved_at,
        }

    @staticmethod
    def _approval_to_domain(document: MongoDocument) -> RepairApproval:
        try:
            return RepairApproval(
                id=_required_str(document, "_id"),
                classroom_id=_required_str(document, "classroom_id"),
                seat_id=_required_str(document, "seat_id"),
                requested_row=_optional_int(document, "requested_row"),
                requested_column=_optional_int(document, "requested_column"),
                requested_by=_required_str(document, "requested_by"),
                requested_at=_required_datetime(document, "requested_at"),
                status=RepairApprovalStatus(_required_str(document, "status")),
                approved_by=_optional_str(document, "approved_by"),
                approved_at=_optional_datetime(document, "approved_at"),
            )
        except (KeyError, TypeError, ValueError):
            raise RepositoryDataError() from None


def _occupancy_to_document(occupancy: SeatCurrentOccupancy) -> MongoDocument:
    return {
        "state": occupancy.state.value,
        "source": occupancy.source.value,
        "confidence": occupancy.confidence,
        "observed_at": occupancy.observed_at,
        "event_id": occupancy.event_id,
    }


def _assignment_to_document(assignment: SeatAssignment) -> MongoDocument:
    return {
        "_id": assignment.seat_id,
        "seat_id": assignment.seat_id,
        "student_id": assignment.student_id,
        "classroom_id": assignment.classroom_id,
        "assigned_at": assignment.assigned_at,
    }


def _assignment_to_domain(document: MongoDocument) -> SeatAssignment:
    try:
        return SeatAssignment(
            seat_id=_required_str(document, "seat_id"),
            student_id=_required_str(document, "student_id"),
            classroom_id=_required_str(document, "classroom_id"),
            assigned_at=_required_datetime(document, "assigned_at"),
        )
    except (KeyError, TypeError, ValueError):
        raise RepositoryDataError() from None


def _is_transient_transaction_error(exc: PyMongoError) -> bool:
    """transaction 재시도 대상 transient 오류인지 확인한다.

    PyMongo 4.x는 transient를 ``AutoReconnect``·``NotPrimaryError`` 또는
    ``OperationFailure``의 ``TransientTransactionError`` label로 표현한다.
    단, commit 시점에는 ``UnknownTransactionCommitResult`` label이 붙어
    commit 결과가 불확실함을 뜻하므로 isinstance만으로 transient로
    오분류하지 않도록 해당 label이 있으면 transient에서 제외한다.
    """
    if exc.has_error_label("UnknownTransactionCommitResult"):
        return False
    if isinstance(exc, (AutoReconnect, NotPrimaryError)):
        return True
    return isinstance(exc, OperationFailure) and exc.has_error_label("TransientTransactionError")


def _is_ambiguous_commit_error(exc: PyMongoError) -> bool:
    """commit 결과가 불확실한 오류인지 확인한다.

    PyMongo 4.x는 ``UnknownTransactionCommitResult`` 전용 클래스 대신
    label로 표현한다. 커밋이 실제 반영됐을 수도, 안 됐을 수도 있다.
    """
    return exc.has_error_label("UnknownTransactionCommitResult")


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


def _optional_int(document: MongoDocument, key: str) -> int | None:
    value = document.get(key)
    if value is not None:
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError
        return value
    return None


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
