"""Thread-safe in-memory classroom repository."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from threading import RLock

from ..errors import (
    ClassroomDuplicateError,
    ClassroomNotFoundError,
    SeatBatchConflictError,
    SeatDuplicateError,
    SeatInactiveForAssignmentError,
    SeatMigrationSnapshotNotFoundError,
    SeatNotFoundError,
)
from ..models import (
    Classroom,
    ClassroomPage,
    RepairApproval,
    RepairApprovalStatus,
    Seat,
    SeatAssignment,
    SeatCurrentOccupancy,
    SeatMigrationRecord,
    SeatMigrationSnapshot,
    SeatMigrationSnapshotPayload,
    SeatObservationBatchRecord,
    SeatOccupancyApplication,
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
            if (
                seat.row is not None
                and seat.column is not None
                and self._seat_by_coordinate(seat.classroom_id, seat.row, seat.column) is not None
            ):
                raise SeatDuplicateError()
            self._seats[seat.id] = seat
            return seat

    def get_seat(self, seat_id: str) -> Seat | None:
        with self._lock:
            return self._seats.get(seat_id)

    def get_seats(self, seat_ids: Sequence[str]) -> dict[str, Seat]:
        """없는 좌석은 결과에서 빠진다. MongoDB의 `$in` 조회와 같은 계약이다."""
        with self._lock:
            return {seat_id: self._seats[seat_id] for seat_id in seat_ids if seat_id in self._seats}

    def list_seats(self, classroom_id: str, *, limit: int, offset: int) -> SeatPage:
        with self._lock:
            items = [
                item
                for item in self._seats.values()
                if item.classroom_id == classroom_id and item.is_active
            ]
        items.sort(key=lambda item: (item.code, item.id))
        return SeatPage(items=items[offset : offset + limit], total=len(items))

    def list_all_seats_for_allocation(self, classroom_id: str) -> list[Seat]:
        """allocator용: 비활성(legacy inactive) 포함 모든 좌석을 돌려준다."""
        with self._lock:
            return [item for item in self._seats.values() if item.classroom_id == classroom_id]

    def replace_seat(self, seat: Seat, *, expected_version: int) -> Seat | None:
        with self._lock:
            current = self._seats.get(seat.id)
            if current is None or current.version != expected_version:
                return None
            self._seats[seat.id] = seat
            return seat

    def update_seat(self, seat: Seat, *, unset_fields: list[str] | None = None) -> Seat:
        # 메모리 저장소는 도메인 객체를 통째로 저장하므로 unset_fields는 무시한다.
        with self._lock:
            existing = self._seats.get(seat.id)
            if existing is None:
                raise SeatNotFoundError()
            duplicate = self._seat_by_code(seat.classroom_id, seat.code)
            if duplicate is not None and duplicate.id != seat.id:
                raise SeatDuplicateError()
            if seat.row is not None and seat.column is not None:
                coordinate_holder = self._seat_by_coordinate(
                    seat.classroom_id, seat.row, seat.column
                )
                if coordinate_holder is not None and coordinate_holder.id != seat.id:
                    raise SeatDuplicateError()
            self._seats[seat.id] = seat
            return seat

    def delete_seat(self, seat_id: str) -> None:
        """좌석 레코드를 물리적으로 제거한다 (hard delete).

        ``is_active=False`` 기록이 아니라 레코드를 지우므로 code와
        (row, column) 예약이 즉시 해제된다. history·batch는 별도 dict라
        영향을 받지 않는다.
        """
        with self._lock:
            existing = self._seats.pop(seat_id, None)
            if existing is None:
                raise SeatNotFoundError()

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

    def get_histories_by_event(self, event_id: str) -> dict[str, SeatOccupancyHistory]:
        with self._lock:
            return {
                item.seat_id: item for item in self._history.values() if item.event_id == event_id
            }

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

    def _seat_by_coordinate(self, classroom_id: str, row: int, column: int) -> Seat | None:
        return next(
            (
                item
                for item in self._seats.values()
                if item.classroom_id == classroom_id and item.row == row and item.column == column
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

    ``store``에 `InMemoryClassroomRepository`를 주면 그 store의 지정 dict와
    lock을 공유한다. UoW와 같은 store를 바라보도록 조립해 mutation이
    조회에 그대로 보이게 한다.
    """

    def __init__(self, store: InMemoryClassroomRepository | None = None) -> None:
        self._store = store
        self._assignments: dict[str, SeatAssignment] = {}
        self._lock = RLock()

    def _target(self) -> tuple[dict[str, SeatAssignment], RLock]:
        if self._store is not None:
            return self._store._assignments, self._store._lock
        return self._assignments, self._lock

    def assign(self, assignment: SeatAssignment) -> SeatAssignment:
        """학생을 좌석에 지정한다. 이미 지정되어 있으면 덮어쓴다."""
        assignments, lock = self._target()
        with lock:
            assignments[assignment.seat_id] = assignment
            return assignment

    def unassign(self, seat_id: str) -> None:
        """좌석-학생 지정을 해제한다."""
        assignments, lock = self._target()
        with lock:
            assignments.pop(seat_id, None)

    def get_by_seat(self, seat_id: str) -> SeatAssignment | None:
        """좌석 ID로 지정을 조회한다."""
        assignments, lock = self._target()
        with lock:
            return assignments.get(seat_id)

    def get_by_student(self, student_id: str, classroom_id: str) -> SeatAssignment | None:
        """학생 ID와 강의실 ID로 지정을 조회한다."""
        assignments, lock = self._target()
        with lock:
            for assignment in assignments.values():
                if assignment.student_id == student_id and assignment.classroom_id == classroom_id:
                    return assignment
            return None

    def list_by_classroom(self, classroom_id: str) -> list[SeatAssignment]:
        """강의실의 모든 지정을 조회한다."""
        assignments, lock = self._target()
        with lock:
            return [
                assignment
                for assignment in assignments.values()
                if assignment.classroom_id == classroom_id
            ]

    def unassign_by_student(self, student_id: str) -> int:
        """학생의 모든 좌석 지정을 해제한다. 해제된 지정 수를 반환한다."""
        assignments, lock = self._target()
        with lock:
            to_remove = [
                seat_id
                for seat_id, assignment in assignments.items()
                if assignment.student_id == student_id
            ]
            for seat_id in to_remove:
                del assignments[seat_id]
            return len(to_remove)


class InMemorySeatMutationUnitOfWork:
    """In-memory 좌석 mutation UoW.

    `InMemoryClassroomRepository`와 같은 store·lock을 공유해 조회·해제·삭제·
    관측 적용이 단일 lock 안에서 원자적으로 수행된다. 경쟁 상황의 결과는 Mongo
    transaction과 동일한 선형화 규칙(마지막 쓰기 승리·지정 해제 후 지정·
    hard delete 승리)을 따른다.
    """

    def __init__(
        self,
        store: InMemoryClassroomRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))

    def assign_or_move_if_active(
        self, seat_id: str, student_id: str, classroom_id: str
    ) -> SeatAssignment:
        """같은 강의실 내 기존 지정을 해제하고 새 좌석에 지정한다 (원자적).

        좌석이 없거나 비활성이면 service와 동일한 오류를 던져
        delete/assign 경쟁에서도 상태 불일치가 남지 않게 한다.
        """
        with self._store._lock:
            seat = self._store.get_seat(seat_id)
            if seat is None:
                raise SeatNotFoundError()
            if not seat.is_active:
                raise SeatInactiveForAssignmentError()
            existing = self._store.get_assignment_by_student(student_id, classroom_id)
            if existing is not None and existing.seat_id != seat_id:
                self._store.unassign(existing.seat_id)
            assignment = SeatAssignment(
                seat_id=seat_id,
                student_id=student_id,
                classroom_id=classroom_id,
                assigned_at=self._clock(),
            )
            return self._store.assign(assignment)

    def delete_seat_and_unassign(self, seat_id: str) -> None:
        """좌석과 현재 지정을 물리 삭제한다 (원자적 hard delete).

        좌석이 없으면 지정도 건드리지 않고 ``SeatNotFoundError``를 던진다.
        history·batch는 지우지 않는다 (cascade 없음).
        """
        with self._store._lock:
            if self._store.get_seat(seat_id) is None:
                raise SeatNotFoundError()
            self._store.unassign(seat_id)
            self._store.delete_seat(seat_id)

    def unassign_if_active(self, seat_id: str) -> None:
        """활성 좌석의 지정만 해제한다. 비활성·미존재 좌석은 무시한다."""
        with self._store._lock:
            seat = self._store.get_seat(seat_id)
            if seat is None or not seat.is_active:
                return
            self._store.unassign(seat_id)

    def append_history_and_apply_occupancy(
        self,
        history: SeatOccupancyHistory,
        *,
        expected_version: int,
        occupancy: SeatCurrentOccupancy | None,
        updated_at: datetime,
    ) -> SeatOccupancyHistory | None:
        """관측 history 기록과 current 반영을 같은 lock 안에서 수행한다.

        delete와 같은 store lock을 쓰므로 좌석 단위로 선형화된다. 좌석이
        사라졌으면 아무것도 쓰지 않고 ``SeatNotFoundError``를, version이
        어긋나면 ``None``을 돌려준다.
        """
        with self._store._lock:
            seat = self._store.get_seat(history.seat_id)
            if seat is None or not seat.is_active:
                raise SeatNotFoundError()
            if seat.version != expected_version:
                return None
            stored = self._store.append_occupancy_history(history)
            if occupancy is not None:
                self._store.replace_seat(
                    replace(
                        seat,
                        current_occupancy=occupancy,
                        updated_at=updated_at,
                        version=seat.version + 1,
                    ),
                    expected_version=expected_version,
                )
            return stored

    def append_histories_and_apply_occupancies(
        self,
        applications: Sequence[SeatOccupancyApplication],
        *,
        updated_at: datetime,
    ) -> tuple[SeatOccupancyHistory, ...] | None:
        """여러 좌석의 관측을 같은 lock 안에서 함께 적용한다.

        **검사를 모두 끝낸 뒤에 쓴다.** 좌석이 하나라도 없거나 version이 어긋나면
        아무것도 쓰지 않아야 하는데, 검사와 쓰기를 섞으면 앞쪽 좌석은 이미 쓰인
        상태로 중간에 실패한다. MongoDB transaction의 all-or-nothing과 같은
        동작을 lock 안에서 재현한다.
        """
        if not applications:
            return ()

        with self._store._lock:
            seats: list[Seat] = []
            for item in applications:
                seat = self._store.get_seat(item.history.seat_id)
                if seat is None or not seat.is_active:
                    raise SeatNotFoundError()
                seats.append(seat)
            for seat, item in zip(seats, applications, strict=True):
                if seat.version != item.expected_version:
                    return None

            stored: list[SeatOccupancyHistory] = []
            for seat, item in zip(seats, applications, strict=True):
                stored.append(self._store.append_occupancy_history(item.history))
                if item.occupancy is not None:
                    self._store.replace_seat(
                        replace(
                            seat,
                            current_occupancy=item.occupancy,
                            updated_at=updated_at,
                            version=seat.version + 1,
                        ),
                        expected_version=item.expected_version,
                    )
            return tuple(stored)


class InMemorySeatMigrationRepository:
    """In-memory 오프라인 migration 저장소.

    snapshot manifest와 복원용 payload를 분리해 보관하고, 좌석별 감사
    기록과 수동 repair 승인 기록을 저장한다. 단일 lock으로 동시 접근을
    직렬화한다.
    """

    def __init__(self) -> None:
        self._snapshots: dict[str, SeatMigrationSnapshot] = {}
        self._payloads: dict[str, SeatMigrationSnapshotPayload] = {}
        self._records: dict[str, SeatMigrationRecord] = {}
        self._approvals: dict[str, RepairApproval] = {}
        self._lock = RLock()

    def save_snapshot(
        self,
        snapshot: SeatMigrationSnapshot,
        *,
        seats: tuple[Seat, ...],
        assignments: tuple[SeatAssignment, ...],
    ) -> SeatMigrationSnapshot:
        with self._lock:
            self._snapshots[snapshot.id] = snapshot
            self._payloads[snapshot.id] = SeatMigrationSnapshotPayload(
                seats=seats,
                assignments=assignments,
            )
            return snapshot

    def get_snapshot(self, snapshot_id: str) -> SeatMigrationSnapshot | None:
        with self._lock:
            return self._snapshots.get(snapshot_id)

    def get_snapshot_payload(self, snapshot_id: str) -> SeatMigrationSnapshotPayload | None:
        with self._lock:
            return self._payloads.get(snapshot_id)

    def latest_snapshot(self, classroom_id: str) -> SeatMigrationSnapshot | None:
        with self._lock:
            candidates = [
                snapshot
                for snapshot in self._snapshots.values()
                if snapshot.classroom_id == classroom_id
            ]
            return max(candidates, key=lambda snapshot: snapshot.created_at) if candidates else None

    def mark_snapshot_restored(self, snapshot_id: str, restored_at: datetime) -> None:
        with self._lock:
            snapshot = self._snapshots.get(snapshot_id)
            if snapshot is None:
                raise SeatMigrationSnapshotNotFoundError()
            self._snapshots[snapshot_id] = replace(snapshot, restored_at=restored_at)

    def save_record(self, record: SeatMigrationRecord) -> SeatMigrationRecord:
        with self._lock:
            self._records[record.id] = record
            return record

    def list_records(
        self, classroom_id: str, *, snapshot_id: str | None = None
    ) -> list[SeatMigrationRecord]:
        with self._lock:
            records = [
                record
                for record in self._records.values()
                if record.classroom_id == classroom_id
                and (snapshot_id is None or record.snapshot_id == snapshot_id)
            ]
            return sorted(records, key=lambda record: record.created_at)

    def save_approval(self, approval: RepairApproval) -> RepairApproval:
        with self._lock:
            self._approvals[approval.id] = approval
            return approval

    def get_approval(self, approval_id: str) -> RepairApproval | None:
        with self._lock:
            return self._approvals.get(approval_id)

    def list_approvals(
        self, classroom_id: str, *, seat_id: str | None = None
    ) -> list[RepairApproval]:
        with self._lock:
            approvals = [
                approval
                for approval in self._approvals.values()
                if approval.classroom_id == classroom_id
                and (seat_id is None or approval.seat_id == seat_id)
            ]
            return sorted(approvals, key=lambda approval: approval.requested_at)

    def approved_approval_for_seat(self, classroom_id: str, seat_id: str) -> RepairApproval | None:
        with self._lock:
            return next(
                (
                    approval
                    for approval in self._approvals.values()
                    if approval.classroom_id == classroom_id
                    and approval.seat_id == seat_id
                    and approval.status == RepairApprovalStatus.APPROVED
                ),
                None,
            )
