"""최소 좌석 MongoDB index와 직렬화 계약."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.classrooms.adapters.mongo_repository import MongoClassroomRepository
from app.classrooms.errors import SeatNotFoundError
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
