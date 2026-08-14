"""최소 좌석 MongoDB index와 직렬화 계약."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from pymongo.errors import DuplicateKeyError

from app.classrooms.adapters.mongo_repository import MongoClassroomRepository
from app.classrooms.errors import SeatDuplicateError, SeatNotFoundError
from app.classrooms.models import (
    Classroom,
    ObservationBatchStatus,
    OccupancySource,
    Seat,
    SeatCurrentOccupancy,
    SeatGeometry,
    SeatObservation,
    SeatObservationBatchRecord,
    SeatOccupancy,
    SeatOccupancyHistory,
)
from app.shared.errors import RepositoryDataError


class RecordingCollection:
    def __init__(self) -> None:
        self.indexes: list[tuple[list[tuple[str, int]], dict[str, object]]] = []
        self.updates: list[tuple[dict[str, object], dict[str, object]]] = []
        self.returned_document: dict[str, object] | None = None

    def create_index(self, fields: list[tuple[str, int]], **options: object) -> None:
        self.indexes.append((fields, options))

    def find_one_and_update(
        self,
        query: dict[str, object],
        update: dict[str, object],
        *,
        return_document: object,
    ) -> dict[str, object] | None:
        del return_document
        self.updates.append((query, update))
        return self.returned_document


class RecordingDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, RecordingCollection] = {}

    def __getitem__(self, name: str) -> RecordingCollection:
        return self.collections.setdefault(name, RecordingCollection())


class MigrationCursor:
    """migrate_seat_row_column가 쓰는 find(...).sort(...) 결과 fake."""

    def __init__(self, documents: list[dict[str, object]]) -> None:
        self._documents = documents

    def sort(self, key: str, direction: int) -> MigrationCursor:
        self._documents = sorted(
            self._documents,
            key=lambda document: str(document[key]),
            reverse=direction == -1,
        )
        return self

    def __iter__(self) -> Iterator[dict[str, object]]:
        return iter(self._documents)


class MigrationCollection:
    """find/update_one만 지원하는 마이그레이션 대상 collection fake.

    find()는 equality 조건만 적용하고, 호출된 query는 ``queries``에 기록한다.
    """

    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.documents = documents
        self.updates: list[tuple[dict[str, object], dict[str, object]]] = []
        self.queries: list[dict[str, object]] = []

    def find(self, query: dict[str, object]) -> MigrationCursor:
        self.queries.append(query)
        filtered = [
            document
            for document in self.documents
            if all(document.get(key) == value for key, value in query.items())
        ]
        return MigrationCursor(filtered)

    def update_one(self, query: dict[str, object], update: dict[str, object]) -> None:
        self.updates.append((query, update))
        set_values = update.get("$set")
        if not isinstance(set_values, dict):
            raise TypeError
        for document in self.documents:
            if document.get("_id") == query.get("_id"):
                document.update(set_values)
                return


class MigrationDatabase:
    def __init__(self, classrooms: list[dict[str, object]]) -> None:
        self.collections: dict[str, MigrationCollection] = {
            MongoClassroomRepository.classroom_collection_name: MigrationCollection(classrooms),
            MongoClassroomRepository.seat_collection_name: MigrationCollection([]),
        }

    def __getitem__(self, name: str) -> MigrationCollection:
        return self.collections[name]


class UniqueIndexSeatCollection:
    """seats collection fake로 (classroom_id, row, column) partial unique index를 흉내낸다.

    - row/column이 모두 number인 문서만 unique 키를 가진다 (null pair는 무충돌).
    - is_active와 무관하게 적용된다 (비활성 좌석도 coordinate를 예약).
    - 위반 시 실제 MongoDB와 같은 DuplicateKeyError를 발생시킨다.
    """

    def __init__(self, documents: list[dict[str, object]] | None = None) -> None:
        self.documents: list[dict[str, object]] = list(documents or [])
        self.insert_calls: list[dict[str, object]] = []
        self.update_calls: list[tuple[dict[str, object], dict[str, object]]] = []

    @staticmethod
    def _coordinate_key(
        document: dict[str, object],
    ) -> tuple[object, object, object] | None:
        row = document.get("row")
        column = document.get("column")
        if row is None or column is None:
            return None
        return (document["classroom_id"], row, column)

    def _conflicting(self, key: tuple[object, object, object], exclude_id: object) -> bool:
        return any(
            document.get("_id") != exclude_id and self._coordinate_key(document) == key
            for document in self.documents
        )

    def insert_one(self, document: dict[str, object]) -> None:
        self.insert_calls.append(document)
        key = self._coordinate_key(document)
        if key is not None and self._conflicting(key, exclude_id=None):
            raise DuplicateKeyError("E11000 duplicate key error")
        self.documents.append(document)

    def find_one(self, query: dict[str, object]) -> dict[str, object] | None:
        return next(
            (
                document
                for document in self.documents
                if all(document.get(key) == value for key, value in query.items())
            ),
            None,
        )

    def find_one_and_update(
        self,
        query: dict[str, object],
        update: dict[str, object],
        *,
        return_document: object,
    ) -> dict[str, object] | None:
        del return_document
        self.update_calls.append((query, update))
        document = self.find_one(query)
        if document is None:
            return None
        set_values = update.get("$set")
        if not isinstance(set_values, dict):
            raise TypeError
        merged = {**document, **set_values}
        unset_values = update.get("$unset")
        if isinstance(unset_values, dict):
            for unset_key in unset_values:
                merged.pop(unset_key, None)
        key = self._coordinate_key(merged)
        if key is not None and self._conflicting(key, exclude_id=document["_id"]):
            raise DuplicateKeyError("E11000 duplicate key error")
        for idx, existing in enumerate(self.documents):
            if existing["_id"] == document["_id"]:
                self.documents[idx] = merged
                break
        return merged


class UniqueIndexDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, object] = {
            MongoClassroomRepository.classroom_collection_name: RecordingCollection(),
            MongoClassroomRepository.seat_collection_name: UniqueIndexSeatCollection(),
            MongoClassroomRepository.batch_collection_name: RecordingCollection(),
            MongoClassroomRepository.history_collection_name: RecordingCollection(),
        }

    def __getitem__(self, name: str) -> object:
        return self.collections[name]


def _values() -> tuple[
    Classroom,
    Seat,
    SeatObservationBatchRecord,
    SeatOccupancyHistory,
]:
    now = datetime(2026, 8, 10, 3, 0, tzinfo=UTC)
    classroom = Classroom(
        id="classroom-id",
        code="A101",
        name="일반 강의실",
        location="A동",
        is_active=True,
        created_at=now,
    )
    seat = Seat(
        id="seat-id",
        classroom_id=classroom.id,
        code="S01",
        label="좌석 1",
        geometry=SeatGeometry(0.1, 0.2, 0.3, 0.4),
        is_active=True,
        current_occupancy=SeatCurrentOccupancy(
            SeatOccupancy.OCCUPIED,
            OccupancySource.SYSTEM,
            0.9,
            now,
            "event-id",
        ),
        created_at=now,
        updated_at=now,
        version=1,
    )
    batch = SeatObservationBatchRecord(
        event_id="event-id",
        classroom_id=classroom.id,
        source=OccupancySource.SYSTEM,
        observed_at=now,
        observations=(SeatObservation(seat.id, True, 0.9),),
        status=ObservationBatchStatus.COMPLETED,
        processed_count=1,
        changed_count=1,
        received_at=now,
        completed_at=now + timedelta(seconds=1),
    )
    history = SeatOccupancyHistory(
        id="history-id",
        seat_id=seat.id,
        classroom_id=classroom.id,
        event_id=batch.event_id,
        source=OccupancySource.SYSTEM,
        from_state=SeatOccupancy.UNKNOWN,
        to_state=SeatOccupancy.OCCUPIED,
        occupied=True,
        confidence=0.9,
        observed_at=now,
        received_at=now,
        applied_to_current=True,
        state_changed=True,
    )
    return classroom, seat, batch, history


def test_indexes_touch_only_four_retained_collections() -> None:
    database = RecordingDatabase()
    MongoClassroomRepository.ensure_indexes(database)  # type: ignore[arg-type]

    assert set(database.collections) == {
        "classrooms",
        "seats",
        "seat_observation_batches",
        "seat_occupancy_history",
    }
    assert any(
        fields == [("code", 1)] and options.get("unique")
        for fields, options in database.collections["classrooms"].indexes
    )
    assert any(
        fields == [("event_id", 1), ("seat_id", 1)] and options.get("unique")
        for fields, options in database.collections["seat_occupancy_history"].indexes
    )


def test_row_column_unique_index_has_partial_filter() -> None:
    database = RecordingDatabase()
    MongoClassroomRepository.ensure_indexes(database)  # type: ignore[arg-type]

    indexes = database.collections["seats"].indexes
    assert any(
        fields == [("classroom_id", 1), ("row", 1), ("column", 1)]
        and options.get("name") == "seats_classroom_row_column_unique"
        and options.get("unique")
        and options.get("partialFilterExpression")
        == {"row": {"$type": "number"}, "column": {"$type": "number"}}
        for fields, options in indexes
    )


def test_seat_document_omits_none_row_column() -> None:
    """row/column이 None이면 문서에서 키를 생략한다 (하위 호환 좌석)."""
    _, seat, _, _ = _values()
    document = MongoClassroomRepository._seat_to_document(seat)
    assert "row" not in document
    assert "column" not in document


def test_seat_document_includes_row_column() -> None:
    """row/column이 있으면 문서에 포함된다."""
    _, seat, _, _ = _values()
    document = MongoClassroomRepository._seat_to_document(
        dataclasses.replace(seat, row=3, column=5)
    )
    assert document["row"] == 3
    assert document["column"] == 5


def test_seat_domain_reads_row_column() -> None:
    """문서의 row/column을 도메인으로 읽는다."""
    _, seat, _, _ = _values()
    document = MongoClassroomRepository._seat_to_document(seat)
    document["row"] = 2
    document["column"] = 4
    restored = MongoClassroomRepository._seat_to_domain(document)
    assert restored.row == 2
    assert restored.column == 4


def test_seat_domain_without_row_column_returns_none() -> None:
    """문서에 row/column이 없으면 None으로 읽는다 (하위 호환)."""
    _, seat, _, _ = _values()
    document = MongoClassroomRepository._seat_to_document(seat)
    restored = MongoClassroomRepository._seat_to_domain(document)
    assert restored.row is None
    assert restored.column is None


@pytest.mark.parametrize(
    ("row_value", "column_value"),
    [
        (True, 1),
        (1, True),
        (1.5, 2),
        (2, "3"),
    ],
)
def test_seat_domain_rejects_non_int_row_column(row_value: object, column_value: object) -> None:
    """row/column이 정수가 아니면 RepositoryDataError를 발생시킨다."""
    _, seat, _, _ = _values()
    document = MongoClassroomRepository._seat_to_document(seat)
    document["row"] = row_value
    document["column"] = column_value
    with pytest.raises(RepositoryDataError):
        MongoClassroomRepository._seat_to_domain(document)


def test_update_seat_unsets_requested_fields() -> None:
    """update_seat가 unset_fields를 $unset으로 전달한다."""
    _, seat, _, _ = _values()
    database = RecordingDatabase()
    repository = MongoClassroomRepository(database)  # type: ignore[arg-type]
    seats = database.collections[MongoClassroomRepository.seat_collection_name]
    seats.returned_document = MongoClassroomRepository._seat_to_document(seat)

    assert repository.update_seat(seat, unset_fields=["row", "column"]) == seat

    update_query, update = seats.updates[0]
    assert update_query == {"_id": seat.id}
    assert set(update) == {"$set", "$unset"}
    assert update["$unset"] == {"row": "", "column": ""}


def test_update_seat_without_unset_fields_uses_set_only() -> None:
    """unset_fields가 없으면 기존처럼 $set만 사용한다."""
    _, seat, _, _ = _values()
    database = RecordingDatabase()
    repository = MongoClassroomRepository(database)  # type: ignore[arg-type]
    seats = database.collections[MongoClassroomRepository.seat_collection_name]
    seats.returned_document = MongoClassroomRepository._seat_to_document(seat)

    assert repository.update_seat(seat) == seat

    _, update = seats.updates[0]
    assert set(update) == {"$set"}


# ============================================================
# (row, column) 좌표 중복 — memory와 parity를 맞추는 Mongo 의미 고정
# ============================================================


def test_mongo_nonnull_duplicate_raises_seat_duplicate() -> None:
    """같은 강의실에서 같은 nonnull (row, column) 생성은 SeatDuplicateError가 된다."""
    database = UniqueIndexDatabase()
    repository = MongoClassroomRepository(database)  # type: ignore[arg-type]
    _, seat, _, _ = _values()
    first = dataclasses.replace(seat, id="seat-1", code="S01", row=1, column=1)
    duplicate = dataclasses.replace(first, id="seat-2", code="S02")

    assert repository.create_seat(first) == first
    with pytest.raises(SeatDuplicateError):
        repository.create_seat(duplicate)


def test_mongo_update_self_coordinate_is_allowed() -> None:
    """자기 좌석의 (row, column)으로의 update는 충돌이 아니다."""
    database = UniqueIndexDatabase()
    repository = MongoClassroomRepository(database)  # type: ignore[arg-type]
    _, seat, _, _ = _values()
    seat = dataclasses.replace(seat, row=1, column=1)
    repository.create_seat(seat)

    updated = dataclasses.replace(seat, row=1, column=1, label="이름만 변경")

    assert repository.update_seat(updated) == updated


def test_mongo_null_pair_duplicate_coordinate_is_allowed() -> None:
    """row/column이 모두 None이면 partial index가 적용되지 않아 중복 생성이 허용된다."""
    database = UniqueIndexDatabase()
    repository = MongoClassroomRepository(database)  # type: ignore[arg-type]
    _, seat, _, _ = _values()
    first = dataclasses.replace(seat, id="seat-1", code="S01", row=None, column=None)
    second = dataclasses.replace(first, id="seat-2", code="S02")

    assert repository.create_seat(first) == first
    assert repository.create_seat(second) == second


def test_mongo_inactive_seat_reserves_coordinate() -> None:
    """비활성 좌석도 (row, column)을 예약한다 (unique index에 is_active가 없다)."""
    database = UniqueIndexDatabase()
    repository = MongoClassroomRepository(database)  # type: ignore[arg-type]
    _, seat, _, _ = _values()
    inactive = dataclasses.replace(seat, id="seat-1", code="S01", row=1, column=1, is_active=False)
    active = dataclasses.replace(inactive, id="seat-2", code="S02", is_active=True)

    assert repository.create_seat(inactive) == inactive
    with pytest.raises(SeatDuplicateError):
        repository.create_seat(active)


def test_retained_documents_roundtrip() -> None:
    classroom, seat, batch, history = _values()
    assert (
        MongoClassroomRepository._classroom_to_domain(
            MongoClassroomRepository._classroom_to_document(classroom)
        )
        == classroom
    )
    assert (
        MongoClassroomRepository._seat_to_domain(MongoClassroomRepository._seat_to_document(seat))
        == seat
    )
    assert (
        MongoClassroomRepository._batch_to_domain(
            MongoClassroomRepository._batch_to_document(batch)
        )
        == batch
    )
    assert (
        MongoClassroomRepository._history_to_domain(
            MongoClassroomRepository._history_to_document(history)
        )
        == history
    )


def test_legacy_extra_fields_are_ignored_without_deleting_data() -> None:
    classroom, _, batch, history = _values()
    classroom_document = MongoClassroomRepository._classroom_to_document(classroom)
    classroom_document.update(
        {
            "timezone": "Asia/Seoul",
            "schedules": [],
            "responsible_staff_user_ids": ["legacy-staff"],
        }
    )
    batch_document = MongoClassroomRepository._batch_to_document(batch)
    batch_document.pop("source")
    batch_document["actor_user_id"] = "legacy-admin"
    history_document = MongoClassroomRepository._history_to_document(history)
    history_document.pop("source")

    assert MongoClassroomRepository._classroom_to_domain(classroom_document) == classroom
    assert MongoClassroomRepository._batch_to_domain(batch_document).source == OccupancySource.MOCK
    assert (
        MongoClassroomRepository._history_to_domain(history_document).source == OccupancySource.MOCK
    )


def test_updates_preserve_unrelated_legacy_fields() -> None:
    _, seat, batch, _ = _values()
    database = RecordingDatabase()
    repository = MongoClassroomRepository(database)  # type: ignore[arg-type]
    seats = database.collections[MongoClassroomRepository.seat_collection_name]
    batches = database.collections[MongoClassroomRepository.batch_collection_name]
    seats.returned_document = MongoClassroomRepository._seat_to_document(seat)
    batches.returned_document = MongoClassroomRepository._batch_to_document(batch)

    assert repository.replace_seat(seat, expected_version=0) == seat
    assert repository.complete_observation_batch(batch) == batch

    for _, update in (*seats.updates, *batches.updates):
        assert set(update) == {"$set"}
        set_fields = update["$set"]
        assert isinstance(set_fields, dict)
        assert "_id" not in set_fields


def test_classroom_update_and_delete_use_set_without_id() -> None:
    classroom, _, _, _ = _values()
    database = RecordingDatabase()
    repository = MongoClassroomRepository(database)  # type: ignore[arg-type]
    classrooms = database.collections[MongoClassroomRepository.classroom_collection_name]
    classrooms.returned_document = MongoClassroomRepository._classroom_to_document(classroom)

    assert repository.update_classroom(classroom) == classroom
    repository.delete_classroom(classroom.id)

    update_query, update = classrooms.updates[0]
    assert update_query == {"_id": classroom.id}
    assert set(update) == {"$set"}
    set_fields = update["$set"]
    assert isinstance(set_fields, dict)
    assert "_id" not in set_fields
    assert set_fields["code"] == classroom.code

    delete_query, delete = classrooms.updates[1]
    assert delete_query == {"_id": classroom.id}
    assert delete == {"$set": {"is_active": False}}


def test_seat_update_and_delete_use_set_without_id() -> None:
    _, seat, _, _ = _values()
    database = RecordingDatabase()
    repository = MongoClassroomRepository(database)  # type: ignore[arg-type]
    seats = database.collections[MongoClassroomRepository.seat_collection_name]
    seats.returned_document = MongoClassroomRepository._seat_to_document(seat)

    assert repository.update_seat(seat) == seat
    repository.delete_seat(seat.id)

    update_query, update = seats.updates[0]
    assert update_query == {"_id": seat.id}
    assert set(update) == {"$set"}
    set_fields = update["$set"]
    assert isinstance(set_fields, dict)
    assert "_id" not in set_fields
    assert set_fields["code"] == seat.code

    delete_query, delete = seats.updates[1]
    assert delete_query == {"_id": seat.id}
    assert delete == {"$set": {"is_active": False}}


def test_seat_update_and_delete_missing_raise_not_found() -> None:
    _, seat, _, _ = _values()
    database = RecordingDatabase()
    repository = MongoClassroomRepository(database)  # type: ignore[arg-type]

    with pytest.raises(SeatNotFoundError):
        repository.update_seat(seat)
    with pytest.raises(SeatNotFoundError):
        repository.delete_seat(seat.id)


def test_naive_datetime_raises_repository_error() -> None:
    _, seat, _, _ = _values()
    document = MongoClassroomRepository._seat_to_document(seat)
    document["updated_at"] = datetime(2026, 8, 10, 3, 0)  # noqa: DTZ001
    with pytest.raises(RepositoryDataError):
        MongoClassroomRepository._seat_to_domain(document)


def _seat_document(seat_id: str, code: str) -> dict[str, object]:
    return {
        "_id": seat_id,
        "classroom_id": "classroom-a101",
        "code": code,
        "label": f"좌석 {code}",
        "is_active": True,
    }


def test_migrate_seat_row_column_assigns_row_column_by_code_order() -> None:
    """코드 오름차순으로 행·열을 초기화한다 (기본 columns_per_row=4)."""
    database = MigrationDatabase([{"_id": "classroom-a101", "is_active": True}])
    seats = database.collections[MongoClassroomRepository.seat_collection_name]
    seats.documents = [
        _seat_document("seat-1", "S01"),
        _seat_document("seat-2", "S02"),
        _seat_document("seat-3", "S03"),
        _seat_document("seat-4", "S04"),
        _seat_document("seat-5", "S05"),
        _seat_document("seat-6", "S06"),
    ]

    updated = MongoClassroomRepository.migrate_seat_row_column(database)  # type: ignore[arg-type]

    assert updated == 6
    rows = {document["_id"]: (document["row"], document["column"]) for document in seats.documents}
    assert rows == {
        "seat-1": (1, 1),
        "seat-2": (1, 2),
        "seat-3": (1, 3),
        "seat-4": (1, 4),
        "seat-5": (2, 1),
        "seat-6": (2, 2),
    }


def test_migrate_seat_row_column_uses_custom_columns_per_row() -> None:
    """columns_per_row를 지정하면 그 값으로 행이 나뉜다."""
    database = MigrationDatabase([{"_id": "classroom-a101", "is_active": True}])
    seats = database.collections[MongoClassroomRepository.seat_collection_name]
    seats.documents = [
        _seat_document("seat-1", "S01"),
        _seat_document("seat-2", "S02"),
        _seat_document("seat-3", "S03"),
        _seat_document("seat-4", "S04"),
    ]

    updated = MongoClassroomRepository.migrate_seat_row_column(
        database,  # type: ignore[arg-type]
        columns_per_row=3,
    )

    assert updated == 4
    rows = {document["_id"]: (document["row"], document["column"]) for document in seats.documents}
    assert rows == {
        "seat-1": (1, 1),
        "seat-2": (1, 2),
        "seat-3": (1, 3),
        "seat-4": (2, 1),
    }


def test_migrate_seat_row_column_skips_seats_with_row_column() -> None:
    """이미 행·열이 있는 좌석은 건드리지 않는다."""
    database = MigrationDatabase([{"_id": "classroom-a101", "is_active": True}])
    seats = database.collections[MongoClassroomRepository.seat_collection_name]
    seats.documents = [
        {**_seat_document("seat-1", "S01"), "row": 9, "column": 9},
        _seat_document("seat-2", "S02"),
        _seat_document("seat-3", "S03"),
    ]

    updated = MongoClassroomRepository.migrate_seat_row_column(database)  # type: ignore[arg-type]

    assert updated == 2
    rows = {document["_id"]: (document["row"], document["column"]) for document in seats.documents}
    # seat-2(S02)는 코드 순서 1번째지만 이미 지정된 seat-1은 유지된다.
    assert rows == {
        "seat-1": (9, 9),
        "seat-2": (1, 2),
        "seat-3": (1, 3),
    }


def test_migrate_seat_row_column_ignores_inactive_classrooms_and_seats() -> None:
    """비활성 강의실·좌석은 대상에서 제외한다."""
    database = MigrationDatabase(
        [
            {"_id": "classroom-a101", "is_active": True},
            {"_id": "classroom-b203", "is_active": False},
        ]
    )
    seats = database.collections[MongoClassroomRepository.seat_collection_name]
    seats.documents = [
        _seat_document("seat-1", "S01"),
        _seat_document("seat-2", "S02"),
        {**_seat_document("seat-3", "S03"), "is_active": False},
        {**_seat_document("seat-4", "S04"), "classroom_id": "classroom-b203"},
    ]

    updated = MongoClassroomRepository.migrate_seat_row_column(database)  # type: ignore[arg-type]

    assert updated == 2
    rows = {
        document["_id"]: (document.get("row"), document.get("column"))
        for document in seats.documents
    }
    assert rows == {
        "seat-1": (1, 1),
        "seat-2": (1, 2),
        "seat-3": (None, None),
        "seat-4": (None, None),
    }
