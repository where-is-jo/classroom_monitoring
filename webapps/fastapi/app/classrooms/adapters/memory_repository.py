"""Thread-safe in-memory classroom repository."""

from __future__ import annotations

from dataclasses import replace
from threading import RLock

from ..errors import (
    ClassroomDuplicateError,
    ClassroomNotFoundError,
    SeatBatchConflictError,
    SeatDuplicateError,
    SeatNotFoundError,
)
from ..models import (
    Classroom,
    ClassroomPage,
    Seat,
    SeatAssignment,
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
        # 좌석-학생 지정 저장소 (key: seat_id)
        self._assignments: dict[str, SeatAssignment] = {}

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

    def update_classroom(self, classroom: Classroom) -> Classroom:
        with self._lock:
            existing = self._classrooms.get(classroom.id)
            if existing is None:
                raise ClassroomNotFoundError()
            duplicate = self.get_classroom_by_code(classroom.code)
            if duplicate is not None and duplicate.id != classroom.id:
                raise ClassroomDuplicateError()
            self._classrooms[classroom.id] = classroom
            return classroom

    def delete_classroom(self, classroom_id: str) -> None:
        with self._lock:
            existing = self._classrooms.get(classroom_id)
            if existing is None:
                raise ClassroomNotFoundError()
            self._classrooms[classroom_id] = replace(existing, is_active=False)

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

    def update_seat(self, seat: Seat) -> Seat:
        with self._lock:
            existing = self._seats.get(seat.id)
            if existing is None:
                raise SeatNotFoundError()
            duplicate = self._seat_by_code(seat.classroom_id, seat.code)
            if duplicate is not None and duplicate.id != seat.id:
                raise SeatDuplicateError()
            self._seats[seat.id] = seat
            return seat

    def delete_seat(self, seat_id: str) -> None:
        with self._lock:
            existing = self._seats.get(seat_id)
            if existing is None:
                raise SeatNotFoundError()
            self._seats[seat_id] = replace(existing, is_active=False)

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

    # ============================================================
    # 좌석-학생 지정
    # ============================================================

    def assign(self, assignment: SeatAssignment) -> SeatAssignment:
        """학생을 좌석에 지정한다. 이미 지정되어 있으면 덮어쓴다."""
        with self._lock:
            self._assignments[assignment.seat_id] = assignment
            return assignment

    def unassign(self, seat_id: str) -> None:
        """좌석-학생 지정을 해제한다."""
        with self._lock:
            self._assignments.pop(seat_id, None)

    def get_assignment_by_seat(self, seat_id: str) -> SeatAssignment | None:
        """좌석 ID로 지정을 조회한다."""
        with self._lock:
            return self._assignments.get(seat_id)

    def get_assignment_by_student(
        self, student_id: str, classroom_id: str
    ) -> SeatAssignment | None:
        """학생 ID와 강의실 ID로 지정을 조회한다."""
        with self._lock:
            for assignment in self._assignments.values():
                if assignment.student_id == student_id and assignment.classroom_id == classroom_id:
                    return assignment
            return None

    def list_assignments_by_classroom(self, classroom_id: str) -> list[SeatAssignment]:
        """강의실의 모든 지정을 조회한다."""
        with self._lock:
            return [
                assignment
                for assignment in self._assignments.values()
                if assignment.classroom_id == classroom_id
            ]

    def unassign_by_student(self, student_id: str) -> int:
        """학생의 모든 좌석 지정을 해제한다. 해제된 지정 수를 반환한다."""
        with self._lock:
            to_remove = [
                seat_id
                for seat_id, assignment in self._assignments.items()
                if assignment.student_id == student_id
            ]
            for seat_id in to_remove:
                del self._assignments[seat_id]
            return len(to_remove)

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


class InMemorySeatAssignmentRepository:
    """좌석-학생 지정 전용 in-memory 저장소.

    `InMemoryClassroomRepository`에 이미 지정 메서드가 있지만,
    ClassroomService가 `SeatAssignmentRepository` 포트를 직접 주입받도록
    별도 어댑터로 분리한다 (포트 메서드명 계약에 따른다).
    """

    def __init__(self) -> None:
        self._assignments: dict[str, SeatAssignment] = {}
        self._lock = RLock()

    def assign(self, assignment: SeatAssignment) -> SeatAssignment:
        """학생을 좌석에 지정한다. 이미 지정되어 있으면 덮어쓴다."""
        with self._lock:
            self._assignments[assignment.seat_id] = assignment
            return assignment

    def unassign(self, seat_id: str) -> None:
        """좌석-학생 지정을 해제한다."""
        with self._lock:
            self._assignments.pop(seat_id, None)

    def get_by_seat(self, seat_id: str) -> SeatAssignment | None:
        """좌석 ID로 지정을 조회한다."""
        with self._lock:
            return self._assignments.get(seat_id)

    def get_by_student(self, student_id: str, classroom_id: str) -> SeatAssignment | None:
        """학생 ID와 강의실 ID로 지정을 조회한다."""
        with self._lock:
            for assignment in self._assignments.values():
                if assignment.student_id == student_id and assignment.classroom_id == classroom_id:
                    return assignment
            return None

    def list_by_classroom(self, classroom_id: str) -> list[SeatAssignment]:
        """강의실의 모든 지정을 조회한다."""
        with self._lock:
            return [
                assignment
                for assignment in self._assignments.values()
                if assignment.classroom_id == classroom_id
            ]

    def unassign_by_student(self, student_id: str) -> int:
        """학생의 모든 좌석 지정을 해제한다. 해제된 지정 수를 반환한다."""
        with self._lock:
            to_remove = [
                seat_id
                for seat_id, assignment in self._assignments.items()
                if assignment.student_id == student_id
            ]
            for seat_id in to_remove:
                del self._assignments[seat_id]
            return len(to_remove)
