"""오프라인 좌석 migration 서비스.

오프라인 cutover(쓰기 drain)에서 수행하는 절차를 구현한다.

- ``preflight_check``: 부분 좌표(행만·열만) 발견 시 zero-write abort.
  활성 null 쌍만 append 대상이고 비활성 null 쌍은 skip 대상으로 식별한다.
- ``create_snapshot``: seats·assignments 쌍의 named checksum snapshot 생성.
  manifest에는 비민감 metadata(checksum·count·시각)만 남기고 내용은 저장소 payload로 보관한다.
- ``run_migration``: 정규화된 code,id 순으로 활성 null 쌍에 좌표를 append하고
  기존 좌표를 보존한다. 재실행 시 이미 좌표가 있는 행은 건너뛴다.
- ``validate_migration``: 좌표·code·assignment 무결성과 (snapshot 대비) active 좌표
  양수·결정성·보존을 검사한다.
- ``restore_snapshot``/``rollback``: gate deviation은 자동 repair 없이 snapshot으로 복원한다.
- ``repair_seat``: 승인·감사 하에 양쪽 모두 승인된 미사용 양수 좌표 또는 양쪽 unset만 허용한다.

KMS/암호화 승인은 ``cutover_ready`` 콜백으로 주입받으며, 승인되지 않으면
migration run(cutover)을 차단한다.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from ..shared.errors import ClassroomInputError
from .errors import (
    ClassroomNotFoundError,
    SeatMigrationCutoverBlockedError,
    SeatMigrationPostGateError,
    SeatMigrationPreflightError,
    SeatMigrationRestoreError,
    SeatMigrationSnapshotNotFoundError,
    SeatNotFoundError,
    SeatRepairInvalidError,
    SeatRepairNotApprovedError,
)
from .models import (
    Classroom,
    RepairApproval,
    RepairApprovalStatus,
    Seat,
    SeatAssignment,
    SeatMigrationAction,
    SeatMigrationRecord,
    SeatMigrationSnapshot,
    SeatMigrationSnapshotPayload,
)
from .ports import ClassroomRepository, SeatAssignmentRepository, SeatMigrationRepository

# allocator와 동일한 좌석 코드 패턴: S 뒤에 양의 정수 1개 이상.
_SEAT_CODE_PATTERN = re.compile(r"^S(\d+)$")

_SNAPSHOT_TTL_DAYS = 7


@dataclass(frozen=True)
class SeatMigrationPreflight:
    """preflight 검사 결과."""

    classroom_id: str
    ok: bool
    total_seats: int
    completed_count: int
    active_null_count: int
    inactive_null_count: int
    partial_coordinates_count: int
    blocked_reason: str | None


@dataclass(frozen=True)
class SeatMigrationResult:
    """migration run 결과."""

    classroom_id: str
    snapshot: SeatMigrationSnapshot
    migrated_count: int
    skipped_with_coordinates_count: int
    inactive_null_skipped_count: int
    max_row: int
    records: tuple[SeatMigrationRecord, ...]


@dataclass(frozen=True)
class SeatMigrationValidation:
    """migration 전후 gate 검증 결과."""

    classroom_id: str
    ok: bool
    issues: tuple[str, ...]
    seats_count: int
    assignments_count: int


@dataclass(frozen=True)
class SeatMigrationStatus:
    """migration 상태 조회 결과."""

    classroom_id: str
    preflight: SeatMigrationPreflight
    snapshot: SeatMigrationSnapshot | None
    records: tuple[SeatMigrationRecord, ...]
    validation: SeatMigrationValidation


def _snapshot_checksum(seats: Sequence[Seat], assignments: Sequence[SeatAssignment]) -> str:
    """seats·assignments 쌍의 정규화 checksum (SHA-256).

    좌석·지정 데이터의 변경(위치·code·활성·지정)을 감지하기 위한 해시다.
    해시는 단방향이므로 manifest에 seat/student ID나 내용이 남지 않는다.
    """
    seat_rows = sorted(
        (seat.id, seat.code, seat.row, seat.column, seat.is_active) for seat in seats
    )
    assignment_rows = sorted(
        (assignment.seat_id, assignment.student_id, assignment.classroom_id)
        for assignment in assignments
    )
    canonical = json.dumps(
        {"seats": seat_rows, "assignments": assignment_rows},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _seat_sort_key(seat: Seat) -> tuple[int, int | str, str]:
    """정규화된 code,id 순 정렬 키.

    ``^S(\\d+)$`` 코드는 숫자로 정규화해 정렬하고(S1·S10·S2 → S1, S2, S10),
    같은 숫자로 정규화되면 id 오름차순으로 정렬한다. 패턴이 아닌 레거시
    코드는 숫자 영역 뒤에 id순으로 배치한다.
    """
    match = _SEAT_CODE_PATTERN.fullmatch(seat.code)
    if match is not None:
        return (0, int(match.group(1)), seat.id)
    return (1, 0, seat.id)


class SeatMigrationService:
    def __init__(
        self,
        repository: ClassroomRepository,
        *,
        migration_repository: SeatMigrationRepository,
        assignment_repository: SeatAssignmentRepository | None = None,
        cutover_ready: Callable[[], bool] | None = None,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._migration_repository = migration_repository
        self._assignment_repository = assignment_repository
        # 승인된 암호화 target/KMS 여부. None이면 게이트를 적용하지 않는다(단위 테스트).
        # 운영 조립(dependencies)에서는 항상 설정 기반 게이트를 주입한다.
        self._cutover_ready = cutover_ready
        self._clock = clock

    # ============================================================
    # preflight
    # ============================================================

    def preflight_check(self, classroom_id: str) -> SeatMigrationPreflight:
        """migration 전 사전 검사.

        모든 좌석(활성·비활성 무관)에서 부분 좌표(행만 또는 열만)를 발견하면
        ``ok=False``가 되고 migration run은 zero-write abort한다.
        활성 null 쌍은 append 대상, 비활성 null 쌍은 skip 대상으로 식별한다.
        """
        classroom = self._required_classroom(classroom_id)
        seats = self._repository.list_all_seats_for_allocation(classroom.id)
        partial = [seat for seat in seats if (seat.row is None) != (seat.column is None)]
        active_nulls = [
            seat for seat in seats if seat.is_active and seat.row is None and seat.column is None
        ]
        inactive_nulls = [
            seat
            for seat in seats
            if not seat.is_active and seat.row is None and seat.column is None
        ]
        completed = [seat for seat in seats if seat.row is not None and seat.column is not None]
        blocked_reason: str | None = None
        if partial:
            blocked_reason = (
                f"부분 좌표 좌석 {len(partial)}개 발견 — 행만 또는 열만 있는 좌석이 "
                "있으면 migration을 시작할 수 없습니다."
            )
        return SeatMigrationPreflight(
            classroom_id=classroom.id,
            ok=not partial,
            total_seats=len(seats),
            completed_count=len(completed),
            active_null_count=len(active_nulls),
            inactive_null_count=len(inactive_nulls),
            partial_coordinates_count=len(partial),
            blocked_reason=blocked_reason,
        )

    # ============================================================
    # snapshot
    # ============================================================

    def create_snapshot(
        self, classroom_id: str, *, name: str | None = None
    ) -> SeatMigrationSnapshot:
        """seats·assignments 쌍의 named checksum snapshot을 만든다.

        manifest에는 count·checksum·시각 같은 비민감 metadata만 담고,
        좌석·지정 내용은 payload로 저장소에 보관한다.
        """
        classroom = self._required_classroom(classroom_id)
        seats = self._repository.list_all_seats_for_allocation(classroom.id)
        assignments = self._assignments(classroom.id)
        now = self._aware_datetime(self._clock())
        snapshot_name = name or f"seat-migration-{now:%Y%m%d-%H%M%S}"
        snapshot = SeatMigrationSnapshot(
            id=str(uuid4()),
            classroom_id=classroom.id,
            name=snapshot_name,
            seats_count=len(seats),
            assignments_count=len(assignments),
            checksum=_snapshot_checksum(seats, assignments),
            created_at=now,
            expires_at=now + timedelta(days=_SNAPSHOT_TTL_DAYS),
            restored_at=None,
        )
        return self._migration_repository.save_snapshot(
            snapshot,
            seats=tuple(seats),
            assignments=tuple(assignments),
        )

    # ============================================================
    # migration run
    # ============================================================

    def run_migration(
        self, classroom_id: str, *, snapshot_name: str | None = None
    ) -> SeatMigrationResult:
        """오프라인 migration을 실행한다.

        1. cutover 게이트: 승인된 암호화 target/KMS가 없으면 차단한다.
        2. preflight: 부분 좌표가 있으면 zero-write abort한다.
        3. backup snapshot 생성 후 좌표를 append한다 (기존 좌표 보존).
        4. append 도중 오류가 나면 절반 완료 상태를 남기지 않고 snapshot으로
           복원한 뒤 ``SeatMigrationPostGateError``/``SeatMigrationRestoreError``로 전파한다.
        5. post-gate 검증에 실패하면 자동 repair 없이 snapshot으로 restore한다.
        """
        classroom = self._required_classroom(classroom_id)
        self._require_cutover_ready()
        preflight = self.preflight_check(classroom.id)
        if not preflight.ok:
            raise SeatMigrationPreflightError(
                preflight.blocked_reason or "preflight 검사에 실패했습니다."
            )

        snapshot = self.create_snapshot(classroom.id, name=snapshot_name)
        ordered = sorted(
            self._repository.list_all_seats_for_allocation(classroom.id),
            key=_seat_sort_key,
        )
        expected = _simulate_migration(ordered)

        migrated = 0
        skipped_with_coordinates = 0
        inactive_null_skipped = 0
        records: list[SeatMigrationRecord] = []
        try:
            for seat, target in zip(ordered, expected, strict=True):
                if seat.row is not None and seat.column is not None:
                    # 완료 좌표(활성·비활성 무관)는 예약 상태를 유지한다. 재실행 시 건너뛴다.
                    skipped_with_coordinates += 1
                    continue
                if not seat.is_active:
                    # 비활성 null 쌍은 reserve/backfill하지 않는다.
                    inactive_null_skipped += 1
                    continue
                assert target.row is not None and target.column is not None
                updated = self._repository.update_seat(
                    replace(
                        seat,
                        row=target.row,
                        column=target.column,
                        updated_at=self._aware_datetime(self._clock()),
                    )
                )
                record = SeatMigrationRecord(
                    id=str(uuid4()),
                    classroom_id=classroom.id,
                    seat_id=updated.id,
                    action=SeatMigrationAction.APPEND,
                    previous_row=seat.row,
                    previous_column=seat.column,
                    new_row=updated.row,
                    new_column=updated.column,
                    created_at=self._aware_datetime(self._clock()),
                    snapshot_id=snapshot.id,
                )
                self._migration_repository.save_record(record)
                records.append(record)
                migrated += 1
        except Exception as exc:
            # append 도중 오류가 나면 일부 좌석만 좌표가 붙은 절반 완료 상태를
            # 남기지 않도록 snapshot으로 복원을 시도한다. 복원 결과에 맞는
            # migration 오류로 변환해 전파한다.
            try:
                self.restore_snapshot(snapshot.id)
            except Exception as restore_exc:
                raise SeatMigrationRestoreError(
                    "migration 실행 도중 오류가 발생했고 snapshot 복원도 실패했습니다. "
                    "좌석 상태를 수동으로 확인해야 합니다."
                ) from restore_exc
            raise SeatMigrationPostGateError(
                "migration 실행 도중 오류가 발생해 snapshot으로 복원했습니다. "
                "일부 좌석만 변경된 상태를 남기지 않았습니다."
            ) from exc

        result = SeatMigrationResult(
            classroom_id=classroom.id,
            snapshot=snapshot,
            migrated_count=migrated,
            skipped_with_coordinates_count=skipped_with_coordinates,
            inactive_null_skipped_count=inactive_null_skipped,
            max_row=max(
                (target.row for target in expected if target.row is not None),
                default=0,
            ),
            records=tuple(records),
        )

        # post-gate: 자동 파괴적 repair 없이 restore만 허용한다.
        validation = self.validate_migration(classroom.id, snapshot_id=snapshot.id)
        if not validation.ok:
            self.restore_snapshot(snapshot.id)
            raise SeatMigrationPostGateError(
                "migration 후 검증에 실패해 snapshot으로 복원했습니다. "
                "수동 repair는 승인·감사 절차를 거쳐야 합니다."
            )
        return result

    # ============================================================
    # 검증
    # ============================================================

    def validate_migration(
        self, classroom_id: str, *, snapshot_id: str | None = None
    ) -> SeatMigrationValidation:
        """migration 전후 gate 검증.

        - 좌표 무결성: 부분 좌표 금지, active 좌표는 양수
        - code 무결성: 강의실 내 code 중복 금지
        - assignment 무결성: 지정이 강의실의 현존 좌석을 가리킨다
        - snapshot이 주어지면 active 좌표의 보존·결정성(시뮬레이션 일치)을 검사
        """
        classroom = self._required_classroom(classroom_id)
        seats = self._repository.list_all_seats_for_allocation(classroom.id)
        assignments = self._assignments(classroom.id)
        issues: list[str] = []

        for seat in seats:
            if (seat.row is None) != (seat.column is None):
                issues.append(f"부분 좌표: {seat.code}")
            if seat.row is not None and (seat.row < 1 or seat.column is None or seat.column < 1):
                issues.append(f"비정상 좌표(1 미만): {seat.code}")

        codes = [seat.code for seat in seats]
        if len(set(codes)) != len(codes):
            issues.append("중복 code 존재")

        coordinates = [
            (seat.row, seat.column)
            for seat in seats
            if seat.row is not None and seat.column is not None
        ]
        if len(set(coordinates)) != len(coordinates):
            issues.append("중복 좌표 존재")

        seat_ids = {seat.id for seat in seats}
        for assignment in assignments:
            if assignment.seat_id not in seat_ids:
                issues.append(f"미존재 좌석 지정: {assignment.seat_id}")

        if snapshot_id is not None:
            payload = self._migration_repository.get_snapshot_payload(snapshot_id)
            if payload is None:
                issues.append("snapshot payload를 찾을 수 없습니다.")
            else:
                self._validate_against_snapshot(payload, seats, issues)

        return SeatMigrationValidation(
            classroom_id=classroom.id,
            ok=not issues,
            issues=tuple(issues),
            seats_count=len(seats),
            assignments_count=len(assignments),
        )

    def _validate_against_snapshot(
        self,
        payload: SeatMigrationSnapshotPayload,
        current_seats: Sequence[Seat],
        issues: list[str],
    ) -> None:
        """snapshot 상태에서의 결정성 시뮬레이션과 현재 상태를 비교한다.

        - snapshot에 좌표가 있던 좌석(active)은 현재도 같은 좌표여야 한다(보존).
        - 활성 null 쌍의 append 위치는 시뮬레이션과 정확히 일치해야 한다(결정성).
        - snapshot 이후 추가·소실된 좌석이 없어야 한다.
        """
        current_by_id = {seat.id: seat for seat in current_seats}
        expected = _simulate_migration(payload.seats)
        expected_ids = {seat.id for seat in expected}
        for expected_seat in expected:
            current = current_by_id.get(expected_seat.id)
            if current is None:
                issues.append(f"snapshot 좌석 소실: {expected_seat.code}")
            elif (current.row, current.column) != (
                expected_seat.row,
                expected_seat.column,
            ):
                issues.append(f"좌표 변경(보존·결정성 위반): {expected_seat.code}")
        for current in current_seats:
            if current.id not in expected_ids:
                issues.append(f"snapshot 이후 추가 좌석: {current.code}")

    # ============================================================
    # restore / rollback
    # ============================================================

    def restore_snapshot(self, snapshot_id: str) -> SeatMigrationSnapshot:
        """snapshot에서 좌석·지정을 전체 복원한다.

        복원 전 payload checksum을 검증하고, 복원 후 좌표·code·assignment
        무결성을 검증한다. 실패하면 ``SeatMigrationRestoreError``를 던진다.
        """
        snapshot = self._migration_repository.get_snapshot(snapshot_id)
        if snapshot is None:
            raise SeatMigrationSnapshotNotFoundError()
        payload = self._migration_repository.get_snapshot_payload(snapshot_id)
        if payload is None:
            raise SeatMigrationSnapshotNotFoundError()
        if _snapshot_checksum(payload.seats, payload.assignments) != snapshot.checksum:
            raise SeatMigrationRestoreError("snapshot checksum이 일치하지 않아 복원을 중단합니다.")

        for seat in payload.seats:
            current = self._repository.get_seat(seat.id)
            unset_fields: list[str] = []
            if seat.row is None:
                unset_fields.append("row")
            if seat.column is None:
                unset_fields.append("column")
            if current is None:
                self._repository.create_seat(seat)
            else:
                self._repository.update_seat(
                    seat,
                    unset_fields=unset_fields if unset_fields else None,
                )

        if self._assignment_repository is not None:
            for assignment in self._assignment_repository.list_by_classroom(snapshot.classroom_id):
                self._assignment_repository.unassign(assignment.seat_id)
            for assignment in payload.assignments:
                self._assignment_repository.assign(assignment)

        now = self._aware_datetime(self._clock())
        self._migration_repository.mark_snapshot_restored(snapshot_id, now)
        restored = self._migration_repository.get_snapshot(snapshot_id)
        if restored is None:
            raise SeatMigrationSnapshotNotFoundError()

        validation = self.validate_migration(snapshot.classroom_id)
        if not validation.ok:
            raise SeatMigrationRestoreError(
                "복원 후 검증에 실패했습니다. " + " | ".join(validation.issues)
            )
        return restored

    def rollback(
        self, classroom_id: str, *, snapshot_id: str | None = None
    ) -> SeatMigrationSnapshot:
        """강의실 migration을 snapshot으로 롤백한다.

        snapshot_id가 없으면 해당 강의실의 가장 최근 snapshot으로 복원한다.
        """
        classroom = self._required_classroom(classroom_id)
        if snapshot_id is not None:
            snapshot = self._migration_repository.get_snapshot(snapshot_id)
            if snapshot is None or snapshot.classroom_id != classroom.id:
                raise SeatMigrationSnapshotNotFoundError()
        else:
            snapshot = self._migration_repository.latest_snapshot(classroom.id)
            if snapshot is None:
                raise SeatMigrationSnapshotNotFoundError()
        return self.restore_snapshot(snapshot.id)

    # ============================================================
    # 수동 repair (승인·감사)
    # ============================================================

    def request_repair(
        self,
        classroom_id: str,
        seat_id: str,
        *,
        row: int | None,
        column: int | None,
        requested_by: str,
    ) -> RepairApproval:
        """좌석 repair 승인 요청을 만든다.

        허용 형태는 양쪽 모두 양수 정수(미사용 확인은 승인·적용 시) 또는
        양쪽 모두 unset(None)뿐이다. 부분·비양수는 즉시 거부한다.
        """
        classroom = self._required_classroom(classroom_id)
        seat = self._required_seat(seat_id, classroom.id)
        self._validate_repair_form(row, column)
        approval = RepairApproval(
            id=str(uuid4()),
            classroom_id=classroom.id,
            seat_id=seat.id,
            requested_row=row,
            requested_column=column,
            requested_by=requested_by,
            requested_at=self._aware_datetime(self._clock()),
            status=RepairApprovalStatus.PENDING,
            approved_by=None,
            approved_at=None,
        )
        return self._migration_repository.save_approval(approval)

    def approve_repair(self, approval_id: str, *, approved_by: str) -> RepairApproval:
        """대기 중인 repair 승인 요청을 승인한다."""
        approval = self._migration_repository.get_approval(approval_id)
        if approval is None:
            raise SeatRepairNotApprovedError("repair 승인 요청을 찾을 수 없습니다.")
        if approval.status != RepairApprovalStatus.PENDING:
            raise SeatRepairInvalidError("이미 처리된 repair 승인 요청입니다.")
        updated = replace(
            approval,
            status=RepairApprovalStatus.APPROVED,
            approved_by=approved_by,
            approved_at=self._aware_datetime(self._clock()),
        )
        return self._migration_repository.save_approval(updated)

    def repair_seat(
        self,
        classroom_id: str,
        seat_id: str,
        *,
        row: int | None,
        column: int | None,
        approved_by: str,
    ) -> Seat:
        """승인된 수동 repair를 적용한다.

        - 좌석별로 승인된(APPROVED) 요청이 있고 승인자와 실행자가 일치해야 한다.
        - 요청 좌표와 승인된 좌표가 같아야 한다.
        - 양쪽 모두 좌표면 미사용(다른 좌석이 예약하지 않은) 양수 좌표여야 한다.
        - 양쪽 모두 unset이면 좌표를 해제한다.
        - 적용 결과는 seat ID별 감사 기록(``SeatMigrationRecord``)으로 남긴다.
        """
        classroom = self._required_classroom(classroom_id)
        seat = self._required_seat(seat_id, classroom.id)
        self._validate_repair_form(row, column)
        approval = self._migration_repository.approved_approval_for_seat(classroom.id, seat.id)
        if approval is None:
            raise SeatRepairNotApprovedError("승인된 repair 요청이 없습니다.")
        if approval.approved_by != approved_by:
            raise SeatRepairNotApprovedError("승인자와 repair 실행자가 다릅니다.")
        if (approval.requested_row, approval.requested_column) != (row, column):
            raise SeatRepairNotApprovedError("승인된 좌표와 다른 repair입니다.")

        if row is not None and column is not None:
            conflict = any(
                other.id != seat.id and other.row == row and other.column == column
                for other in self._repository.list_all_seats_for_allocation(classroom.id)
            )
            if conflict:
                raise SeatRepairInvalidError("이미 다른 좌석이 사용 중인 좌표입니다.")

        now = self._aware_datetime(self._clock())
        unset_fields: list[str] = []
        if row is None:
            unset_fields.append("row")
        if column is None:
            unset_fields.append("column")
        updated = self._repository.update_seat(
            replace(seat, row=row, column=column, updated_at=now),
            unset_fields=unset_fields if unset_fields else None,
        )
        record = SeatMigrationRecord(
            id=str(uuid4()),
            classroom_id=classroom.id,
            seat_id=seat.id,
            action=SeatMigrationAction.REPAIR,
            previous_row=seat.row,
            previous_column=seat.column,
            new_row=updated.row,
            new_column=updated.column,
            created_at=now,
            snapshot_id=None,
        )
        self._migration_repository.save_record(record)
        return updated

    # ============================================================
    # 상태 조회
    # ============================================================

    def migration_status(self, classroom_id: str) -> SeatMigrationStatus:
        """migration preflight·snapshot·감사 기록·gate 검증을 종합해 돌려준다."""
        classroom = self._required_classroom(classroom_id)
        preflight = self.preflight_check(classroom.id)
        snapshot = self._migration_repository.latest_snapshot(classroom.id)
        records = self._migration_repository.list_records(classroom.id)
        validation = self.validate_migration(classroom.id)
        return SeatMigrationStatus(
            classroom_id=classroom.id,
            preflight=preflight,
            snapshot=snapshot,
            records=tuple(records),
            validation=validation,
        )

    # ============================================================
    # 내부 헬퍼
    # ============================================================

    def _require_cutover_ready(self) -> None:
        if self._cutover_ready is not None and not self._cutover_ready():
            raise SeatMigrationCutoverBlockedError(
                "승인된 암호화 target/KMS가 없어 cutover를 차단합니다."
            )

    def _assignments(self, classroom_id: str) -> list[SeatAssignment]:
        if self._assignment_repository is None:
            return []
        return self._assignment_repository.list_by_classroom(classroom_id)

    def _required_classroom(self, classroom_id: str) -> Classroom:
        classroom = self._repository.get_classroom(classroom_id)
        if classroom is None or not classroom.is_active:
            raise ClassroomNotFoundError()
        return classroom

    def _required_seat(self, seat_id: str, classroom_id: str) -> Seat:
        seat = self._repository.get_seat(seat_id)
        if seat is None or seat.classroom_id != classroom_id:
            raise SeatNotFoundError()
        return seat

    @staticmethod
    def _validate_repair_form(row: int | None, column: int | None) -> None:
        """수동 repair 허용 형태 검증.

        양쪽 모두 양수 정수 또는 양쪽 모두 unset만 허용한다 (부분 입력 금지).
        """
        if row is None and column is None:
            return
        if row is None or column is None:
            raise SeatRepairInvalidError("repair 좌표는 양쪽 모두 지정하거나 모두 해제해야 합니다.")
        if row < 1 or column < 1:
            raise SeatRepairInvalidError("repair 좌표는 1 이상이어야 합니다.")

    @staticmethod
    def _aware_datetime(value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ClassroomInputError("시각은 timezone을 포함해야 합니다.")
        return value.astimezone(UTC)


def _simulate_migration(seats: Sequence[Seat]) -> list[Seat]:
    """pre-migration 좌석 목록에서 migration 후 상태를 결정적으로 계산한다.

    - 정규화된 code,id 순으로 정렬한다.
    - 완료 좌표(활성·비활성 무관)와 비활성 null 쌍은 그대로 둔다.
    - 활성 null 쌍만 예약된 최대 row+ordinal, column=1로 append한다.
    """
    ordered = sorted(seats, key=_seat_sort_key)
    max_row = max(
        (seat.row for seat in ordered if seat.row is not None),
        default=0,
    )
    ordinal = 0
    result: list[Seat] = []
    for seat in ordered:
        if seat.row is not None and seat.column is not None:
            result.append(seat)
            continue
        if not seat.is_active:
            result.append(seat)
            continue
        ordinal += 1
        result.append(replace(seat, row=max_row + ordinal, column=1))
    return result
