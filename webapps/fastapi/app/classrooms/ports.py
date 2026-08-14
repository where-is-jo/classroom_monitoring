"""강의실과 좌석 metadata 저장소 포트."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .models import (
    Classroom,
    ClassroomPage,
    RepairApproval,
    Seat,
    SeatAssignment,
    SeatCurrentOccupancy,
    SeatMigrationRecord,
    SeatMigrationSnapshot,
    SeatMigrationSnapshotPayload,
    SeatObservationBatchRecord,
    SeatOccupancyHistory,
    SeatPage,
)


class ClassroomRepository(Protocol):
    def create_classroom(self, classroom: Classroom) -> Classroom: ...
    def get_classroom(self, classroom_id: str) -> Classroom | None: ...
    def get_classroom_by_code(self, code: str) -> Classroom | None: ...
    def list_classrooms(self, *, limit: int, offset: int) -> ClassroomPage: ...
    def update_classroom(self, classroom: Classroom) -> Classroom: ...
    def delete_classroom(self, classroom_id: str) -> None: ...

    def create_seat(self, seat: Seat) -> Seat: ...
    def get_seat(self, seat_id: str) -> Seat | None: ...
    def list_seats(self, classroom_id: str, *, limit: int, offset: int) -> SeatPage: ...
    def list_all_seats_for_allocation(self, classroom_id: str) -> list[Seat]:
        """allocator용: 비활성 포함 강의실의 모든 좌석을 돌려준다."""
        ...

    def update_seat(self, seat: Seat, *, unset_fields: list[str] | None = None) -> Seat: ...
    def delete_seat(self, seat_id: str) -> None:
        """좌석 레코드를 물리적으로 삭제한다 (hard delete).

        레코드가 사라지므로 code와 (row, column)이 즉시 해제된다.
        history·observation batch는 별도 컬렉션이라 보존된다.
        """
        ...

    def replace_seat(self, seat: Seat, *, expected_version: int) -> Seat | None: ...

    def claim_observation_batch(
        self, record: SeatObservationBatchRecord
    ) -> SeatObservationBatchRecord: ...
    def get_observation_batch(self, event_id: str) -> SeatObservationBatchRecord | None: ...
    def complete_observation_batch(
        self, record: SeatObservationBatchRecord
    ) -> SeatObservationBatchRecord: ...
    def get_history_by_event_and_seat(
        self, event_id: str, seat_id: str
    ) -> SeatOccupancyHistory | None: ...
    def append_occupancy_history(self, history: SeatOccupancyHistory) -> SeatOccupancyHistory: ...


class SeatAssignmentRepository(Protocol):
    """좌석-학생 지정 저장소 추상화."""

    def assign(self, assignment: SeatAssignment) -> SeatAssignment:
        """학생을 좌석에 지정한다."""
        ...

    def unassign(self, seat_id: str) -> None:
        """좌석-학생 지정을 해제한다."""
        ...

    def get_by_seat(self, seat_id: str) -> SeatAssignment | None:
        """좌석 ID로 지정을 조회한다."""
        ...

    def get_by_student(self, student_id: str, classroom_id: str) -> SeatAssignment | None:
        """학생 ID와 강의실 ID로 지정을 조회한다."""
        ...

    def list_by_classroom(self, classroom_id: str) -> list[SeatAssignment]:
        """강의실의 모든 지정을 조회한다."""
        ...

    def unassign_by_student(self, student_id: str) -> int:
        """학생의 모든 좌석 지정을 해제한다. 해제된 지정 수를 반환한다."""
        ...


class SeatMutationUnitOfWork(Protocol):
    """좌석 mutation을 원자적으로 수행하는 단위 작업.

    서비스는 이 UoW만으로 좌석·지정을 mutation하고, assignment 저장소를
    직접 mutation하지 않는다. 각 연산은 저장소 경계에서 원자적이어야 한다.
    관측 적용도 같은 session/lock을 쓰므로 hard delete와 선형화된다.
    """

    def assign_or_move_if_active(
        self, seat_id: str, student_id: str, classroom_id: str
    ) -> SeatAssignment:
        """학생을 좌석에 지정한다.

        같은 강의실의 다른 좌석에 이미 지정된 학생이면 기존 지정을 해제하고
        새 좌석에 지정한다. 좌석이 없으면 ``SeatNotFoundError``, 비활성이면
        ``SeatInactiveForAssignmentError``를 던진다.
        """
        ...

    def delete_seat_and_unassign(self, seat_id: str) -> None:
        """좌석과 현재 지정을 같은 transaction/lock에서 물리 삭제한다 (hard delete).

        ``is_active``에 false를 기록하지 않고 레코드 자체를 지우므로 code와
        (row, column)이 즉시 해제된다. ``seat_occupancy_history``와
        ``seat_observation_batches``는 보존하며 cascade 삭제하지 않는다.
        좌석이 없으면 ``SeatNotFoundError``를 던진다.
        """
        ...

    def unassign_if_active(self, seat_id: str) -> None:
        """활성 좌석의 지정만 해제한다. 비활성·미존재 좌석은 무시한다."""
        ...

    def append_history_and_apply_occupancy(
        self,
        history: SeatOccupancyHistory,
        *,
        expected_version: int,
        occupancy: SeatCurrentOccupancy | None,
        updated_at: datetime,
    ) -> SeatOccupancyHistory | None:
        """관측 history 기록과 current 반영을 원자적으로 수행한다.

        hard delete와 같은 session/lock을 쓰므로 두 연산은 좌석 단위로
        선형화된다.

        - 좌석이 없거나 비활성이면 **아무것도 쓰지 않고** ``SeatNotFoundError``를
          던진다 (동시 delete가 이긴다).
        - 좌석 version이 ``expected_version``과 다르면 아무것도 쓰지 않고
          ``None``을 돌려준다 (호출자가 최신 상태로 재시도한다).
        - ``occupancy``가 ``None``이면 history만 기록한다 (과거 관측 재생).
        - 같은 (event_id, seat_id) history가 이미 있으면 기존 값을 돌려주고,
          내용이 다르면 ``SeatBatchConflictError``를 던진다.
        """
        ...


class SeatMigrationRepository(Protocol):
    """오프라인 migration snapshot·record·approval 저장소.

    snapshot manifest(비민감)와 복원용 payload(민감 데이터)를 분리해 보관하고,
    좌석별 감사 기록과 수동 repair 승인 기록을 저장한다.
    """

    def save_snapshot(
        self,
        snapshot: SeatMigrationSnapshot,
        *,
        seats: tuple[Seat, ...],
        assignments: tuple[SeatAssignment, ...],
    ) -> SeatMigrationSnapshot:
        """snapshot manifest와 payload를 저장한다."""
        ...

    def get_snapshot(self, snapshot_id: str) -> SeatMigrationSnapshot | None:
        """snapshot manifest를 조회한다."""
        ...

    def get_snapshot_payload(self, snapshot_id: str) -> SeatMigrationSnapshotPayload | None:
        """snapshot의 좌석·지정 payload를 조회한다."""
        ...

    def latest_snapshot(self, classroom_id: str) -> SeatMigrationSnapshot | None:
        """강의실의 가장 최근 snapshot을 조회한다."""
        ...

    def mark_snapshot_restored(self, snapshot_id: str, restored_at: datetime) -> None:
        """snapshot에 복원 시각을 기록한다."""
        ...

    def save_record(self, record: SeatMigrationRecord) -> SeatMigrationRecord:
        """좌석별 migration 감사 기록을 저장한다."""
        ...

    def list_records(
        self, classroom_id: str, *, snapshot_id: str | None = None
    ) -> list[SeatMigrationRecord]:
        """강의실의 감사 기록을 조회한다."""
        ...

    def save_approval(self, approval: RepairApproval) -> RepairApproval:
        """수동 repair 승인 기록을 저장한다."""
        ...

    def get_approval(self, approval_id: str) -> RepairApproval | None:
        """승인 기록을 조회한다."""
        ...

    def list_approvals(
        self, classroom_id: str, *, seat_id: str | None = None
    ) -> list[RepairApproval]:
        """강의실(또는 좌석)의 승인 기록을 조회한다."""
        ...

    def approved_approval_for_seat(self, classroom_id: str, seat_id: str) -> RepairApproval | None:
        """좌석의 승인된 repair 요청을 조회한다."""
        ...
