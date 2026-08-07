"""Thread-safe in-memory classroom repository."""

from __future__ import annotations

from datetime import date, datetime
from threading import RLock

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
    Seat,
    SeatObservationBatchRecord,
    SeatOccupancyHistory,
    SeatOccupancyHistoryPage,
    SeatPage,
)


class InMemoryClassroomRepository:
    def __init__(self) -> None:
        self._classrooms: dict[str, Classroom] = {}
        self._seats: dict[str, Seat] = {}
        self._batches: dict[str, SeatObservationBatchRecord] = {}
        self._history: dict[str, SeatOccupancyHistory] = {}
        self._alerts: dict[str, AfterHoursAlert] = {}
        self._lock = RLock()

    def create_classroom(self, classroom: Classroom) -> Classroom:
        with self._lock:
            operation_owner = self.get_classroom_by_operation_id(classroom.created_operation_id)
            if operation_owner is not None:
                return operation_owner
            if self.get_classroom_by_code(classroom.code) is not None:
                raise ClassroomDuplicateError()
            self._classrooms[classroom.id] = classroom
            return classroom

    def get_classroom(self, classroom_id: str) -> Classroom | None:
        with self._lock:
            return self._classrooms.get(classroom_id)

    def dashboard_snapshot(
        self,
    ) -> tuple[
        list[Classroom],
        list[Seat],
        list[SeatOccupancyHistory],
        list[AfterHoursAlert],
    ]:
        """Return an immutable-value snapshot for the local admin read model."""
        with self._lock:
            return (
                list(self._classrooms.values()),
                list(self._seats.values()),
                list(self._history.values()),
                list(self._alerts.values()),
            )

    def get_classroom_by_code(self, code: str) -> Classroom | None:
        with self._lock:
            return next(
                (item for item in self._classrooms.values() if item.code == code),
                None,
            )

    def get_classroom_by_operation_id(self, operation_id: str) -> Classroom | None:
        with self._lock:
            return next(
                (item for item in self._classrooms.values() if operation_id in item.operation_ids),
                None,
            )

    def list_classrooms(self, *, include_inactive: bool, limit: int, offset: int) -> ClassroomPage:
        with self._lock:
            items = list(self._classrooms.values())
        if not include_inactive:
            items = [item for item in items if item.is_active]
        items.sort(key=lambda item: (item.code, item.id))
        return ClassroomPage(items=items[offset : offset + limit], total=len(items))

    def replace_classroom(self, classroom: Classroom, *, expected_version: int) -> Classroom | None:
        with self._lock:
            current = self._classrooms.get(classroom.id)
            if current is None or current.version != expected_version:
                owner = self.get_classroom_by_operation_id(classroom.last_operation_id)
                return owner if owner and owner.id == classroom.id else None
            duplicate = self.get_classroom_by_code(classroom.code)
            if duplicate is not None and duplicate.id != classroom.id:
                raise ClassroomDuplicateError()
            self._classrooms[classroom.id] = classroom
            return classroom

    def create_seat(self, seat: Seat) -> Seat:
        with self._lock:
            operation_owner = self.get_seat_by_operation_id(seat.created_operation_id)
            if operation_owner is not None:
                return operation_owner
            if self._seat_by_code(seat.classroom_id, seat.code) is not None:
                raise SeatDuplicateError()
            self._seats[seat.id] = seat
            return seat

    def get_seat(self, seat_id: str) -> Seat | None:
        with self._lock:
            return self._seats.get(seat_id)

    def get_seat_by_operation_id(self, operation_id: str) -> Seat | None:
        with self._lock:
            return next(
                (item for item in self._seats.values() if operation_id in item.operation_ids),
                None,
            )

    def list_seats(
        self,
        classroom_id: str,
        *,
        include_inactive: bool,
        limit: int,
        offset: int,
    ) -> SeatPage:
        with self._lock:
            items = [item for item in self._seats.values() if item.classroom_id == classroom_id]
        if not include_inactive:
            items = [item for item in items if item.is_active]
        items.sort(key=lambda item: (item.code, item.id))
        return SeatPage(items=items[offset : offset + limit], total=len(items))

    def replace_seat(self, seat: Seat, *, expected_version: int) -> Seat | None:
        with self._lock:
            current = self._seats.get(seat.id)
            if current is None or current.version != expected_version:
                owner = self.get_seat_by_operation_id(seat.last_operation_id)
                return owner if owner and owner.id == seat.id else None
            duplicate = self._seat_by_code(seat.classroom_id, seat.code)
            if duplicate is not None and duplicate.id != seat.id:
                raise SeatDuplicateError()
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
        with self._lock:
            items = [item for item in self._history.values() if item.classroom_id == classroom_id]
        if seat_id is not None:
            items = [item for item in items if item.seat_id == seat_id]
        if from_time is not None:
            items = [item for item in items if item.observed_at >= from_time]
        if to_time is not None:
            items = [item for item in items if item.observed_at < to_time]
        items.sort(key=lambda item: (item.observed_at, item.id), reverse=True)
        return SeatOccupancyHistoryPage(items=items[offset : offset + limit], total=len(items))

    def create_alert(self, alert: AfterHoursAlert) -> tuple[AfterHoursAlert, bool]:
        with self._lock:
            existing = next(
                (item for item in self._alerts.values() if item.dedupe_key == alert.dedupe_key),
                None,
            )
            if existing is not None:
                return existing, False
            self._alerts[alert.id] = alert
            return alert, True

    def get_alert(self, alert_id: str) -> AfterHoursAlert | None:
        with self._lock:
            return self._alerts.get(alert_id)

    def get_alert_by_operation_id(self, operation_id: str) -> AfterHoursAlert | None:
        with self._lock:
            return next(
                (item for item in self._alerts.values() if operation_id in item.operation_ids),
                None,
            )

    def list_alerts(
        self,
        *,
        status: AfterHoursAlertStatus | None,
        classroom_id: str | None,
        business_date: date | None,
        limit: int,
        offset: int,
    ) -> AfterHoursAlertPage:
        with self._lock:
            items = list(self._alerts.values())
        if status is not None:
            items = [item for item in items if item.status == status]
        if classroom_id is not None:
            items = [item for item in items if item.classroom_id == classroom_id]
        if business_date is not None:
            items = [item for item in items if item.business_date == business_date]
        items.sort(
            key=lambda item: (
                item.status != AfterHoursAlertStatus.OPEN,
                -item.detected_at.timestamp(),
                item.id,
            )
        )
        return AfterHoursAlertPage(items=items[offset : offset + limit], total=len(items))

    def replace_alert(
        self, alert: AfterHoursAlert, *, expected_version: int
    ) -> AfterHoursAlert | None:
        with self._lock:
            current = self._alerts.get(alert.id)
            if current is None or current.version != expected_version:
                owner = self.get_alert_by_operation_id(alert.last_operation_id)
                return owner if owner and owner.id == alert.id else None
            self._alerts[alert.id] = alert
            return alert

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
        and left.observed_at == right.observed_at
        and left.observations == right.observations
    )
