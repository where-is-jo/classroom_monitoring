"""Thread-safe in-memory classroom repository."""

from __future__ import annotations

from threading import RLock

from ..errors import ClassroomDuplicateError, SeatBatchConflictError, SeatDuplicateError
from ..models import (
    Classroom,
    ClassroomPage,
    Seat,
    SeatObservationBatchRecord,
    SeatOccupancyHistory,
    SeatPage,
)


class InMemoryClassroomRepository:
    def __init__(self) -> None:
        self._classrooms: dict[str, Classroom] = {}
        self._seats: dict[str, Seat] = {}
        self._batches: dict[str, SeatObservationBatchRecord] = {}
        self._history: dict[str, SeatOccupancyHistory] = {}
        self._lock = RLock()

    def create_classroom(self, classroom: Classroom) -> Classroom:
        with self._lock:
            existing = self._classrooms.get(classroom.id)
            if existing is not None:
                if existing != classroom:
                    raise ClassroomDuplicateError()
                return existing
            if self.get_classroom_by_code(classroom.code) is not None:
                raise ClassroomDuplicateError()
            self._classrooms[classroom.id] = classroom
            return classroom

    def get_classroom(self, classroom_id: str) -> Classroom | None:
        with self._lock:
            return self._classrooms.get(classroom_id)

    def get_classroom_by_code(self, code: str) -> Classroom | None:
        with self._lock:
            return next(
                (item for item in self._classrooms.values() if item.code == code),
                None,
            )

    def list_classrooms(self, *, limit: int, offset: int) -> ClassroomPage:
        with self._lock:
            items = [item for item in self._classrooms.values() if item.is_active]
        items.sort(key=lambda item: (item.code, item.id))
        return ClassroomPage(items=items[offset : offset + limit], total=len(items))

    def create_seat(self, seat: Seat) -> Seat:
        with self._lock:
            existing = self._seats.get(seat.id)
            if existing is not None:
                if existing != seat:
                    raise SeatDuplicateError()
                return existing
            if self._seat_by_code(seat.classroom_id, seat.code) is not None:
                raise SeatDuplicateError()
            self._seats[seat.id] = seat
            return seat

    def get_seat(self, seat_id: str) -> Seat | None:
        with self._lock:
            return self._seats.get(seat_id)

    def list_seats(self, classroom_id: str, *, limit: int, offset: int) -> SeatPage:
        with self._lock:
            items = [
                item
                for item in self._seats.values()
                if item.classroom_id == classroom_id and item.is_active
            ]
        items.sort(key=lambda item: (item.code, item.id))
        return SeatPage(items=items[offset : offset + limit], total=len(items))

    def replace_seat(self, seat: Seat, *, expected_version: int) -> Seat | None:
        with self._lock:
            current = self._seats.get(seat.id)
            if current is None or current.version != expected_version:
                return None
            self._seats[seat.id] = seat
            return seat

    def claim_observation_batch(
        self, record: SeatObservationBatchRecord
    ) -> SeatObservationBatchRecord:
        with self._lock:
            existing = self._batches.get(record.event_id)
            if existing is not None:
                if not _same_batch(existing, record):
                    raise SeatBatchConflictError()
                return existing
            self._batches[record.event_id] = record
            return record

    def get_observation_batch(self, event_id: str) -> SeatObservationBatchRecord | None:
        with self._lock:
            return self._batches.get(event_id)

    def complete_observation_batch(
        self, record: SeatObservationBatchRecord
    ) -> SeatObservationBatchRecord:
        with self._lock:
            existing = self._batches.get(record.event_id)
            if existing is None or not _same_batch(existing, record):
                raise SeatBatchConflictError()
            if existing.status == record.status:
                return existing
            self._batches[record.event_id] = record
            return record

    def get_history_by_event_and_seat(
        self, event_id: str, seat_id: str
    ) -> SeatOccupancyHistory | None:
        with self._lock:
            return next(
                (
                    item
                    for item in self._history.values()
                    if item.event_id == event_id and item.seat_id == seat_id
                ),
                None,
            )

    def append_occupancy_history(self, history: SeatOccupancyHistory) -> SeatOccupancyHistory:
        with self._lock:
            existing = self.get_history_by_event_and_seat(history.event_id, history.seat_id)
            if existing is not None:
                if existing != history:
                    raise SeatBatchConflictError()
                return existing
            self._history[history.id] = history
            return history

    def _seat_by_code(self, classroom_id: str, code: str) -> Seat | None:
        return next(
            (
                item
                for item in self._seats.values()
                if item.classroom_id == classroom_id and item.code == code
            ),
            None,
        )


def _same_batch(left: SeatObservationBatchRecord, right: SeatObservationBatchRecord) -> bool:
    return (
        left.event_id == right.event_id
        and left.classroom_id == right.classroom_id
        and left.source == right.source
        and left.observed_at == right.observed_at
        and left.observations == right.observations
    )
