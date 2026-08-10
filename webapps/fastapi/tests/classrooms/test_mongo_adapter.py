"""Mongo index and serialization contract tests for classroom data."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.classrooms.adapters.mongo_repository import MongoClassroomRepository
from app.classrooms.models import (
    AfterHoursAlert,
    AfterHoursAlertStatus,
    Classroom,
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
)
from app.shared.errors import RepositoryDataError


class RecordingCollection:
    def __init__(self) -> None:
        self.indexes: list[tuple[list[tuple[str, int]], dict[str, object]]] = []

    def create_index(self, fields: list[tuple[str, int]], **options: object) -> None:
        self.indexes.append((fields, options))


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
    AfterHoursAlert,
]:
    now = datetime(2026, 8, 5, 8, 0, tzinfo=UTC)
    classroom = Classroom(
        id="classroom-id",
        code="ROOM-1",
        name="Classroom",
        location="Building",
        timezone="Asia/Seoul",
        schedules=(ClassroomSchedule(2, time(9), time(17)),),
        after_hours_grace_minutes=10,
        is_active=True,
        created_at=now,
        updated_at=now,
        version=1,
        created_operation_id="classroom-create",
        last_operation_id="classroom-update",
        operation_ids=("classroom-create", "classroom-update"),
        responsible_staff_user_ids=("staff-id",),
    )
    seat = Seat(
        id="seat-id",
        classroom_id=classroom.id,
        code="A-1",
        label="Seat A-1",
        geometry=SeatGeometry(0.1, 0.2, 0.3, 0.4),
        is_active=True,
        current_occupancy=SeatCurrentOccupancy(
            SeatOccupancy.OCCUPIED, OccupancySource.MOCK, 0.9, now, "event-id"
        ),
        created_at=now,
        updated_at=now,
        version=1,
        created_operation_id="seat-create",
        last_operation_id="seat-observation:event-id:seat-id",
        operation_ids=("seat-create", "seat-observation:event-id:seat-id"),
    )
    batch = SeatObservationBatchRecord(
        event_id="event-id",
        classroom_id=classroom.id,
        actor_user_id="admin-id",
        observed_at=now,
        observations=(SeatObservation(seat.id, True, 0.9),),
        status=ObservationBatchStatus.COMPLETED,
        processed_count=1,
        changed_count=1,
        alert_count=1,
        received_at=now,
        completed_at=now + timedelta(seconds=1),
    )
    history = SeatOccupancyHistory(
        id="history-id",
        seat_id=seat.id,
        classroom_id=classroom.id,
        event_id=batch.event_id,
        from_state=SeatOccupancy.UNKNOWN,
        to_state=SeatOccupancy.OCCUPIED,
        occupied=True,
        confidence=0.9,
        observed_at=now,
        received_at=now,
        applied_to_current=True,
        state_changed=True,
    )
    alert = AfterHoursAlert(
        id="alert-id",
        dedupe_key="classroom-id:seat-id:2026-08-05:after_hours",
        classroom_id=classroom.id,
        seat_id=seat.id,
        business_date=date(2026, 8, 5),
        status=AfterHoursAlertStatus.OPEN,
        detected_at=now,
        resolved_at=None,
        resolved_by_user_id=None,
        created_operation_id="alert-create",
        last_operation_id="alert-create",
        operation_ids=("alert-create",),
        version=0,
    )
    return classroom, seat, batch, history, alert


def test_classroom_indexes_cover_uniqueness_filters_history_and_alerts() -> None:
    database = RecordingDatabase()
    MongoClassroomRepository.ensure_indexes(database)  # type: ignore[arg-type]

    classroom_indexes = database.collections["classrooms"].indexes
    seat_indexes = database.collections["seats"].indexes
    history_indexes = database.collections["seat_occupancy_history"].indexes
    alert_indexes = database.collections["after_hours_alerts"].indexes
    assert any(
        fields == [("code", 1)] and options.get("unique") for fields, options in classroom_indexes
    )
    assert any(
        fields == [("classroom_id", 1), ("code", 1)] and options.get("unique")
        for fields, options in seat_indexes
    )
    assert any(
        fields == [("event_id", 1), ("seat_id", 1)] and options.get("unique")
        for fields, options in history_indexes
    )
    assert any(
        fields == [("dedupe_key", 1)] and options.get("unique") for fields, options in alert_indexes
    )


def test_all_classroom_documents_roundtrip() -> None:
    classroom, seat, batch, history, alert = _values()
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
    assert (
        MongoClassroomRepository._alert_to_domain(
            MongoClassroomRepository._alert_to_document(alert)
        )
        == alert
    )


def test_legacy_classroom_document_defaults_responsible_staff_to_empty() -> None:
    classroom, _, _, _, _ = _values()
    document = MongoClassroomRepository._classroom_to_document(classroom)
    document.pop("responsible_staff_user_ids")

    restored = MongoClassroomRepository._classroom_to_domain(document)

    assert restored.responsible_staff_user_ids == ()


def test_invalid_nested_document_and_naive_datetime_raise_repository_error() -> None:
    classroom, seat, _, _, _ = _values()
    classroom_document = MongoClassroomRepository._classroom_to_document(classroom)
    classroom_document["schedules"] = "not-a-list"
    with pytest.raises(RepositoryDataError):
        MongoClassroomRepository._classroom_to_domain(classroom_document)

    seat_document = MongoClassroomRepository._seat_to_document(seat)
    seat_document["updated_at"] = datetime(2026, 8, 5, 8, 0)  # noqa: DTZ001
    with pytest.raises(RepositoryDataError):
        MongoClassroomRepository._seat_to_domain(seat_document)
