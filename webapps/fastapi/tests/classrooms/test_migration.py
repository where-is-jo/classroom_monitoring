"""TASK-003: 오프라인 migration 통합 테스트.

- preflight: 부분 좌표(행만·열만) zero-write abort, active/inactive null 식별
- migration run: 정규화된 code,id 순 append, 기존 좌표 보존, 재실행 skip,
  active null만 append, inactive null은 skip
- snapshot: named checksum 생성·검증·복원, manifest PII 미포함
- gate: deviation 감지 → 자동 repair 없이 restore
- repair: 승인·감사 하의 두 허용 형태와 seat ID 감사
- cutover 게이트: 승인된 암호화 target/KMS 없이 차단
- API: preflight/run/rollback/status 엔드포인트
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from app.classrooms.adapters.memory_repository import (
    InMemoryClassroomRepository,
    InMemorySeatAssignmentRepository,
    InMemorySeatMigrationRepository,
)
from app.classrooms.adapters.mongo_repository import MongoSeatMigrationRepository
from app.classrooms.errors import (
    SeatMigrationCutoverBlockedError,
    SeatMigrationPostGateError,
    SeatMigrationPreflightError,
    SeatMigrationRestoreError,
    SeatMigrationSnapshotNotFoundError,
    SeatRepairInvalidError,
    SeatRepairNotApprovedError,
)
from app.classrooms.migration import SeatMigrationService, _simulate_migration, _snapshot_checksum
from app.classrooms.models import (
    Classroom,
    OccupancySource,
    RepairApproval,
    RepairApprovalStatus,
    Seat,
    SeatAssignment,
    SeatCurrentOccupancy,
    SeatMigrationAction,
    SeatMigrationRecord,
    SeatMigrationSnapshot,
    SeatOccupancy,
)
from app.main import app
from app.shared.dependencies import get_seat_migration_service
from app.shared.errors import RepositoryUnavailableError

_FIXED_NOW = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)


def _clock() -> datetime:
    return _FIXED_NOW


def _seat(
    seat_id: str,
    code: str,
    *,
    row: int | None = None,
    column: int | None = None,
    is_active: bool = True,
    classroom_id: str = "cls-001",
    label: str | None = None,
) -> Seat:
    return Seat(
        id=seat_id,
        classroom_id=classroom_id,
        code=code,
        label=label or f"좌석 {code}",
        row=row,
        column=column,
        geometry=None,
        is_active=is_active,
        current_occupancy=SeatCurrentOccupancy(
            state=SeatOccupancy.UNKNOWN,
            source=OccupancySource.SYSTEM,
            confidence=None,
            observed_at=None,
            event_id=None,
        ),
        created_at=_FIXED_NOW,
        updated_at=_FIXED_NOW,
        version=0,
    )


@dataclass
class Env:
    store: InMemoryClassroomRepository
    assignment_repo: InMemorySeatAssignmentRepository
    migration_repo: InMemorySeatMigrationRepository
    service: SeatMigrationService

    def create_seat(self, seat: Seat) -> Seat:
        return self.store.create_seat(seat)

    def seats(self) -> dict[str, Seat]:
        return {seat.id: seat for seat in self.store.list_all_seats_for_allocation("cls-001")}


def _build_env(*, cutover_ready: bool | None = None) -> Env:
    store = InMemoryClassroomRepository()
    store.create_classroom(
        Classroom(
            id="cls-001",
            code="R101",
            name="강의실1",
            location="본관",
            is_active=True,
            created_at=_FIXED_NOW,
        )
    )
    assignment_repo = InMemorySeatAssignmentRepository(store=store)
    migration_repo = InMemorySeatMigrationRepository()
    service = SeatMigrationService(
        store,
        migration_repository=migration_repo,
        assignment_repository=assignment_repo,
        cutover_ready=None if cutover_ready is None else (lambda: cutover_ready),
        clock=_clock,
    )
    return Env(
        store=store,
        assignment_repo=assignment_repo,
        migration_repo=migration_repo,
        service=service,
    )


def _assign(env: Env, seat_id: str, student_id: str, classroom_id: str = "cls-001") -> None:
    env.assignment_repo.assign(
        SeatAssignment(
            seat_id=seat_id,
            student_id=student_id,
            classroom_id=classroom_id,
            assigned_at=_FIXED_NOW,
        )
    )


# ============================================================
# preflight
# ============================================================


class TestPreflight:
    def test_preflight_ok_for_completed_and_nulls(self) -> None:
        env = _build_env()
        env.create_seat(_seat("seat-completed", "S01", row=1, column=1))
        env.create_seat(_seat("seat-active-null", "S02"))
        env.create_seat(_seat("seat-inactive-null", "S03", is_active=False))

        preflight = env.service.preflight_check("cls-001")

        assert preflight.ok
        assert preflight.total_seats == 3
        assert preflight.completed_count == 1
        assert preflight.active_null_count == 1
        assert preflight.inactive_null_count == 1
        assert preflight.partial_coordinates_count == 0
        assert preflight.blocked_reason is None

    def test_preflight_aborts_on_active_partial_row(self) -> None:
        env = _build_env()
        env.create_seat(_seat("seat-partial", "S01", row=1))

        preflight = env.service.preflight_check("cls-001")

        assert not preflight.ok
        assert preflight.partial_coordinates_count == 1
        assert preflight.blocked_reason is not None

    def test_preflight_aborts_on_inactive_partial_column(self) -> None:
        """비활성 좌석의 부분 좌표도 발견 즉시 abort 대상이다."""
        env = _build_env()
        env.create_seat(_seat("seat-partial", "S01", column=1, is_active=False))

        preflight = env.service.preflight_check("cls-001")

        assert not preflight.ok
        assert preflight.partial_coordinates_count == 1

    def test_run_with_partial_coordinates_is_zero_write_abort(self) -> None:
        """부분 좌표가 있으면 run은 아무것도 쓰지 않고 abort한다."""
        env = _build_env()
        completed = env.create_seat(_seat("seat-completed", "S01", row=1, column=1))
        partial = env.create_seat(_seat("seat-partial", "S02", row=2))
        null_seat = env.create_seat(_seat("seat-null", "S03"))

        with pytest.raises(SeatMigrationPreflightError):
            env.service.run_migration("cls-001")

        current = env.seats()
        assert current[completed.id] == completed
        assert current[partial.id] == partial
        assert current[null_seat.id] == null_seat
        # preflight 실패 시 backup snapshot조차 만들어지지 않는다 (zero write).
        assert env.migration_repo.latest_snapshot("cls-001") is None
        assert env.migration_repo.list_records("cls-001") == []


# ============================================================
# migration run
# ============================================================


class TestRunMigration:
    def test_run_appends_active_null_in_code_id_order(self) -> None:
        env = _build_env()
        env.create_seat(_seat("seat-s01", "S01", row=1, column=1))
        env.create_seat(_seat("seat-s02", "S02"))
        env.create_seat(_seat("seat-s03", "S03"))

        result = env.service.run_migration("cls-001")

        assert result.migrated_count == 2
        assert result.skipped_with_coordinates_count == 1
        assert result.inactive_null_skipped_count == 0
        assert result.max_row == 3
        current = env.seats()
        assert (current["seat-s01"].row, current["seat-s01"].column) == (1, 1)
        assert (current["seat-s02"].row, current["seat-s02"].column) == (2, 1)
        assert (current["seat-s03"].row, current["seat-s03"].column) == (3, 1)

    def test_run_preserves_existing_coordinates_including_inactive(self) -> None:
        """완료 좌표는 활성·비활성 무관하게 예약 상태를 유지한다."""
        env = _build_env()
        env.create_seat(_seat("seat-active", "S01", row=2, column=3))
        env.create_seat(_seat("seat-inactive", "S02", row=5, column=4, is_active=False))
        env.create_seat(_seat("seat-null", "S03"))

        env.service.run_migration("cls-001")

        current = env.seats()
        assert (current["seat-active"].row, current["seat-active"].column) == (2, 3)
        assert (current["seat-inactive"].row, current["seat-inactive"].column) == (5, 4)
        # 예약된 최대 row(5)+1을 사용해 append한다.
        assert (current["seat-null"].row, current["seat-null"].column) == (6, 1)

    def test_run_skips_inactive_null(self) -> None:
        """비활성 null 쌍은 reserve/backfill하지 않는다."""
        env = _build_env()
        env.create_seat(_seat("seat-active", "S01"))
        env.create_seat(_seat("seat-inactive", "S02", is_active=False))

        result = env.service.run_migration("cls-001")

        assert result.migrated_count == 1
        assert result.inactive_null_skipped_count == 1
        current = env.seats()
        assert (current["seat-active"].row, current["seat-active"].column) == (1, 1)
        assert current["seat-inactive"].row is None
        assert current["seat-inactive"].column is None

    def test_run_rerun_skips_already_migrated_rows(self) -> None:
        """재실행 시 이미 좌표가 있는(마이그레이션된) 행은 건너뛴다."""
        env = _build_env()
        env.create_seat(_seat("seat-s01", "S01"))
        env.create_seat(_seat("seat-s02", "S02"))

        first = env.service.run_migration("cls-001")
        second = env.service.run_migration("cls-001")

        assert first.migrated_count == 2
        assert second.migrated_count == 0
        assert second.skipped_with_coordinates_count == 2
        current = env.seats()
        assert (current["seat-s01"].row, current["seat-s01"].column) == (1, 1)
        assert (current["seat-s02"].row, current["seat-s02"].column) == (2, 1)

    def test_run_orders_by_normalized_code(self) -> None:
        """S10·S2·S1 같은 legacy 코드도 숫자로 정규화해 S1, S2, S10 순으로 append한다."""
        env = _build_env()
        env.create_seat(_seat("seat-s10", "S10"))
        env.create_seat(_seat("seat-s2", "S2"))
        env.create_seat(_seat("seat-s1", "S1"))

        result = env.service.run_migration("cls-001")

        assert result.migrated_count == 3
        current = env.seats()
        assert (current["seat-s1"].row, current["seat-s1"].column) == (1, 1)
        assert (current["seat-s2"].row, current["seat-s2"].column) == (2, 1)
        assert (current["seat-s10"].row, current["seat-s10"].column) == (3, 1)

    def test_run_orders_by_id_after_code_normalization(self) -> None:
        """같은 숫자로 정규화되는 코드(S1·S01)는 id 오름차순으로 예약한다."""
        env = _build_env()
        # "S1"과 "S01"은 모두 숫자 1로 정규화되므로 id 순으로 정렬된다.
        env.create_seat(_seat("seat-b", "S01"))
        env.create_seat(_seat("seat-a", "S1"))

        env.service.run_migration("cls-001")

        current = env.seats()
        assert (current["seat-a"].row, current["seat-a"].column) == (1, 1)
        assert (current["seat-b"].row, current["seat-b"].column) == (2, 1)

    def test_run_records_append_audit_with_snapshot(self) -> None:
        """append 좌석마다 snapshot_id가 붙은 APPEND 감사 기록이 남는다."""
        env = _build_env()
        env.create_seat(_seat("seat-s01", "S01", row=1, column=1))
        env.create_seat(_seat("seat-s02", "S02"))

        result = env.service.run_migration("cls-001")

        snapshot = env.migration_repo.get_snapshot(result.snapshot.id)
        assert snapshot is not None
        assert snapshot.seats_count == 2
        records = env.migration_repo.list_records("cls-001")
        assert len(records) == 1
        record = records[0]
        assert record.seat_id == "seat-s02"
        assert record.action == SeatMigrationAction.APPEND
        assert record.snapshot_id == result.snapshot.id
        assert record.previous_row is None
        assert (record.new_row, record.new_column) == (2, 1)

    def test_run_orders_deterministically_identical_to_simulation(self) -> None:
        """run 결과는 시뮬레이션 결과와 정확히 일치한다 (결정성)."""
        env = _build_env()
        env.create_seat(_seat("seat-s01", "S01", row=3, column=1))
        env.create_seat(_seat("seat-s02", "S02"))
        env.create_seat(_seat("seat-s03", "S03", is_active=False))

        before = env.store.list_all_seats_for_allocation("cls-001")
        simulated = _simulate_migration(before)

        env.service.run_migration("cls-001")

        current = env.seats()
        for seat in simulated:
            assert (current[seat.id].row, current[seat.id].column) == (seat.row, seat.column)


# ============================================================
# migration run — 부분 쓰기 실패 복구 (TASK-003 MAJOR Finding 1)
# ============================================================


class TestRunMigrationFailureRecovery:
    def test_append_failure_restores_snapshot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """append 도중 저장소 오류가 나면 절반 완료 상태를 남기지 않고 snapshot으로 복원한다."""
        env = _build_env()
        env.create_seat(_seat("seat-s01", "S01"))
        env.create_seat(_seat("seat-s02", "S02"))
        env.create_seat(_seat("seat-s03", "S03"))

        original_update = env.store.update_seat
        calls = {"count": 0}

        def flaky_update(seat: Seat, *, unset_fields: list[str] | None = None) -> Seat:
            calls["count"] += 1
            if calls["count"] == 2:
                raise RepositoryUnavailableError()
            return original_update(seat, unset_fields=unset_fields)

        monkeypatch.setattr(env.store, "update_seat", flaky_update)

        with pytest.raises(SeatMigrationPostGateError) as exc_info:
            env.service.run_migration("cls-001")

        assert exc_info.value.code == "SEAT_MIGRATION_POST_GATE_FAILED"
        # 절반 완료 상태가 남지 않는다 — 모든 좌석이 snapshot 상태로 되돌아온다.
        current = env.seats()
        assert current["seat-s01"].row is None
        assert current["seat-s02"].row is None
        assert current["seat-s03"].row is None
        snapshot = env.migration_repo.latest_snapshot("cls-001")
        assert snapshot is not None
        assert snapshot.restored_at == _FIXED_NOW

    def test_append_failure_and_restore_failure_raises_restore_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """append 실패 후 복원까지 실패하면 SeatMigrationRestoreError로 전파한다."""
        env = _build_env()
        env.create_seat(_seat("seat-s01", "S01"))
        env.create_seat(_seat("seat-s02", "S02"))

        original_update = env.store.update_seat
        calls = {"count": 0}

        def flaky_update(seat: Seat, *, unset_fields: list[str] | None = None) -> Seat:
            calls["count"] += 1
            if calls["count"] >= 2:
                raise RepositoryUnavailableError()
            return original_update(seat, unset_fields=unset_fields)

        monkeypatch.setattr(env.store, "update_seat", flaky_update)

        with pytest.raises(SeatMigrationRestoreError) as exc_info:
            env.service.run_migration("cls-001")

        assert exc_info.value.code == "SEAT_MIGRATION_RESTORE_FAILED"
        assert env.migration_repo.latest_snapshot("cls-001") is not None


# ============================================================
# cutover 게이트 (KMS/암호화 승인)
# ============================================================


class TestCutoverGate:
    def test_run_blocked_without_approved_encryption(self) -> None:
        env = _build_env(cutover_ready=False)
        env.create_seat(_seat("seat-s01", "S01"))

        with pytest.raises(SeatMigrationCutoverBlockedError) as exc_info:
            env.service.run_migration("cls-001")

        assert exc_info.value.code == "SEAT_MIGRATION_BLOCKED"
        # 차단 시 snapshot·기록·좌표 변경이 전혀 없다 (zero write).
        assert env.migration_repo.latest_snapshot("cls-001") is None
        assert env.seats()["seat-s01"].row is None

    def test_run_proceeds_when_encryption_approved(self) -> None:
        env = _build_env(cutover_ready=True)
        env.create_seat(_seat("seat-s01", "S01"))

        result = env.service.run_migration("cls-001")

        assert result.migrated_count == 1

    def test_gate_not_configured_does_not_block(self) -> None:
        """게이트를 주입하지 않은 단위 환경에서는 차단하지 않는다."""
        env = _build_env(cutover_ready=None)
        env.create_seat(_seat("seat-s01", "S01"))

        result = env.service.run_migration("cls-001")

        assert result.migrated_count == 1


# ============================================================
# snapshot
# ============================================================


class TestSnapshot:
    def test_create_snapshot_has_counts_and_checksum(self) -> None:
        env = _build_env()
        env.create_seat(_seat("seat-s01", "S01", row=1, column=1))
        env.create_seat(_seat("seat-s02", "S02"))
        _assign(env, "seat-s02", "stu-001")

        snapshot = env.service.create_snapshot("cls-001", name="backup-1")

        assert snapshot.name == "backup-1"
        assert snapshot.seats_count == 2
        assert snapshot.assignments_count == 1
        assert len(snapshot.checksum) == 64  # SHA-256 hex
        assert int(snapshot.checksum, 16) >= 0
        assert snapshot.expires_at > snapshot.created_at
        assert snapshot.restored_at is None

    def test_snapshot_checksum_is_deterministic(self) -> None:
        env = _build_env()
        env.create_seat(_seat("seat-s01", "S01", row=1, column=1))
        env.create_seat(_seat("seat-s02", "S02"))
        _assign(env, "seat-s02", "stu-001")

        first = env.service.create_snapshot("cls-001", name="a")
        second = env.service.create_snapshot("cls-001", name="b")

        assert first.checksum == second.checksum

    def test_snapshot_checksum_changes_when_seats_change(self) -> None:
        env = _build_env()
        env.create_seat(_seat("seat-s01", "S01", row=1, column=1))
        env.create_seat(_seat("seat-s02", "S02"))
        before = env.service.create_snapshot("cls-001", name="before")
        env.service.run_migration("cls-001")
        after = env.service.create_snapshot("cls-001", name="after")

        assert before.checksum != after.checksum

    def test_snapshot_manifest_has_no_pii_or_content(self) -> None:
        """manifest에 seat/student ID, label, archive path, encryption material이 없다."""
        env = _build_env()
        env.create_seat(_seat("seat-s01", "S01", row=1, column=1))
        env.create_seat(_seat("seat-s02", "S02", label="김학생 이름 자리"))
        _assign(env, "seat-s02", "stu-999")

        snapshot = env.service.create_snapshot("cls-001", name="backup")

        manifest = json.dumps(dataclasses.asdict(snapshot), default=str)
        assert "seat-s01" not in manifest
        assert "seat-s02" not in manifest
        assert "stu-999" not in manifest
        assert "김학생" not in manifest
        assert "archive" not in manifest
        assert "path" not in manifest
        assert "encryption" not in manifest

    def test_restore_snapshot_restores_coordinates(self) -> None:
        """migration으로 바뀐 좌표가 snapshot 상태로 되돌아온다 (전체 복원)."""
        env = _build_env()
        env.create_seat(_seat("seat-s01", "S01", row=1, column=1))
        env.create_seat(_seat("seat-s02", "S02"))
        snapshot = env.service.create_snapshot("cls-001", name="backup")

        env.service.run_migration("cls-001")
        assert env.seats()["seat-s02"].row == 2

        restored = env.service.restore_snapshot(snapshot.id)

        assert restored.id == snapshot.id
        assert restored.restored_at == _FIXED_NOW
        current = env.seats()
        assert (current["seat-s01"].row, current["seat-s01"].column) == (1, 1)
        assert current["seat-s02"].row is None
        assert current["seat-s02"].column is None

    def test_restore_snapshot_restores_assignments(self) -> None:
        """snapshot 이후 바뀐 지정이 snapshot 시점으로 되돌아온다."""
        env = _build_env()
        env.create_seat(_seat("seat-s01", "S01", row=1, column=1))
        _assign(env, "seat-s01", "stu-001")
        snapshot = env.service.create_snapshot("cls-001", name="backup")

        env.assignment_repo.unassign("seat-s01")
        _assign(env, "seat-s01", "stu-002")

        env.service.restore_snapshot(snapshot.id)

        assignment = env.assignment_repo.get_by_seat("seat-s01")
        assert assignment is not None
        assert assignment.student_id == "stu-001"

    def test_restore_unknown_snapshot_raises(self) -> None:
        env = _build_env()
        with pytest.raises(SeatMigrationSnapshotNotFoundError):
            env.service.restore_snapshot("missing-snapshot")

    def test_restore_detects_checksum_tampering(self) -> None:
        """payload가 훼손되면 checksum 검증이 실패해 복원을 중단한다."""
        env = _build_env()
        env.create_seat(_seat("seat-s01", "S01", row=1, column=1))
        snapshot = env.service.create_snapshot("cls-001", name="backup")

        payload = env.migration_repo.get_snapshot_payload(snapshot.id)
        assert payload is not None
        tampered_seats = tuple(
            dataclasses.replace(seat, row=(seat.row or 0) + 99) for seat in payload.seats
        )
        env.migration_repo._payloads[snapshot.id] = dataclasses.replace(
            payload, seats=tampered_seats
        )

        with pytest.raises(SeatMigrationRestoreError):
            env.service.restore_snapshot(snapshot.id)

    def test_rollback_restores_latest_snapshot(self) -> None:
        """rollback은 강의실의 최근 snapshot으로 복원한다."""
        env = _build_env()
        env.create_seat(_seat("seat-s01", "S01"))
        env.create_seat(_seat("seat-s02", "S02"))

        result = env.service.run_migration("cls-001")
        assert env.seats()["seat-s01"].row == 1

        restored = env.service.rollback("cls-001")

        assert restored.id == result.snapshot.id
        assert env.seats()["seat-s01"].row is None
        assert env.seats()["seat-s02"].row is None

    def test_rollback_unknown_classroom_404(self) -> None:
        env = _build_env()
        from app.classrooms.errors import ClassroomNotFoundError

        with pytest.raises(ClassroomNotFoundError):
            env.service.rollback("missing-classroom")


# ============================================================
# 검증 (gate)
# ============================================================


class TestValidation:
    def test_validate_ok_after_run(self) -> None:
        env = _build_env()
        env.create_seat(_seat("seat-s01", "S01", row=1, column=1))
        env.create_seat(_seat("seat-s02", "S02"))
        _assign(env, "seat-s02", "stu-001")

        result = env.service.run_migration("cls-001")
        validation = env.service.validate_migration("cls-001", snapshot_id=result.snapshot.id)

        assert validation.ok
        assert validation.issues == ()
        assert validation.seats_count == 2
        assert validation.assignments_count == 1

    def test_validate_detects_partial_coordinate(self) -> None:
        env = _build_env()
        env.create_seat(_seat("seat-s01", "S01", row=1, column=1))
        env.store._seats["seat-bad"] = _seat("seat-bad", "S02", row=2)

        validation = env.service.validate_migration("cls-001")

        assert not validation.ok
        assert any("부분 좌표" in issue for issue in validation.issues)

    def test_validate_detects_duplicate_code(self) -> None:
        env = _build_env()
        env.create_seat(_seat("seat-s01", "S01", row=1, column=1))
        env.store._seats["seat-dup"] = _seat("seat-dup", "S01", row=2, column=2)

        validation = env.service.validate_migration("cls-001")

        assert not validation.ok
        assert any("중복 code" in issue for issue in validation.issues)

    def test_validate_detects_duplicate_coordinate(self) -> None:
        env = _build_env()
        env.create_seat(_seat("seat-s01", "S01", row=1, column=1))
        env.store._seats["seat-dup"] = _seat("seat-dup", "S02", row=1, column=1)

        validation = env.service.validate_migration("cls-001")

        assert not validation.ok
        assert any("중복 좌표" in issue for issue in validation.issues)

    def test_validate_detects_assignment_to_missing_seat(self) -> None:
        env = _build_env()
        env.create_seat(_seat("seat-s01", "S01", row=1, column=1))
        _assign(env, "seat-missing", "stu-001")

        validation = env.service.validate_migration("cls-001")

        assert not validation.ok
        assert any("미존재 좌석 지정" in issue for issue in validation.issues)

    def test_gate_deviation_restores_without_auto_repair(self) -> None:
        """gate deviation(좌표 변경)은 자동 repair 없이 snapshot으로 restore한다."""
        env = _build_env()
        env.create_seat(_seat("seat-s01", "S01"))
        env.create_seat(_seat("seat-s02", "S02"))
        env.service.run_migration("cls-001")

        # migration 후 baseline snapshot을 남기고 drain 중 좌표를 어긋나게 한다.
        baseline = env.service.create_snapshot("cls-001", name="post-migration")
        seat = env.store.get_seat("seat-s01")
        assert seat is not None
        env.store.update_seat(dataclasses.replace(seat, row=99, column=99))

        validation = env.service.validate_migration("cls-001", snapshot_id=baseline.id)
        assert not validation.ok
        assert any("좌표 변경" in issue for issue in validation.issues)

        env.service.restore_snapshot(baseline.id)

        current = env.seats()
        assert (current["seat-s01"].row, current["seat-s01"].column) == (1, 1)
        assert (current["seat-s02"].row, current["seat-s02"].column) == (2, 1)
        # 자동 repair(REPAIR 감사)는 없었다 — restore만 수행됐다.
        actions = [record.action for record in env.migration_repo.list_records("cls-001")]
        assert actions == [SeatMigrationAction.APPEND, SeatMigrationAction.APPEND]


# ============================================================
# 수동 repair (승인·감사)
# ============================================================


class TestRepair:
    def test_repair_requires_approval(self) -> None:
        env = _build_env()
        env.create_seat(_seat("seat-s01", "S01"))

        with pytest.raises(SeatRepairNotApprovedError):
            env.service.repair_seat("cls-001", "seat-s01", row=5, column=1, approved_by="admin")

    def test_repair_rejects_partial_form(self) -> None:
        env = _build_env()
        env.create_seat(_seat("seat-s01", "S01"))

        with pytest.raises(SeatRepairInvalidError):
            env.service.request_repair(
                "cls-001", "seat-s01", row=5, column=None, requested_by="admin"
            )

    def test_repair_rejects_non_positive_coordinates(self) -> None:
        env = _build_env()
        env.create_seat(_seat("seat-s01", "S01"))

        with pytest.raises(SeatRepairInvalidError):
            env.service.request_repair("cls-001", "seat-s01", row=0, column=1, requested_by="admin")

    def test_repair_approved_coordinates_applied_with_audit(self) -> None:
        """양쪽 모두 승인된 미사용 양수 좌표로 repair하고 seat ID 감사를 남긴다."""
        env = _build_env()
        env.create_seat(_seat("seat-s01", "S01"))

        approval = env.service.request_repair(
            "cls-001", "seat-s01", row=5, column=1, requested_by="operator"
        )
        approved = env.service.approve_repair(approval.id, approved_by="admin")
        updated = env.service.repair_seat(
            "cls-001", "seat-s01", row=5, column=1, approved_by="admin"
        )

        assert approved.status == RepairApprovalStatus.APPROVED
        assert approved.approved_by == "admin"
        assert (updated.row, updated.column) == (5, 1)
        records = env.migration_repo.list_records("cls-001")
        assert len(records) == 1
        assert records[0].seat_id == "seat-s01"
        assert records[0].action == SeatMigrationAction.REPAIR
        assert (records[0].new_row, records[0].new_column) == (5, 1)
        approvals = env.migration_repo.list_approvals("cls-001", seat_id="seat-s01")
        assert len(approvals) == 1
        assert approvals[0].status == RepairApprovalStatus.APPROVED

    def test_repair_unset_form_clears_coordinates(self) -> None:
        """양쪽 unset(None)도 허용 형태다 — 좌표를 해제한다."""
        env = _build_env()
        env.create_seat(_seat("seat-s01", "S01", row=2, column=2))

        approval = env.service.request_repair(
            "cls-001", "seat-s01", row=None, column=None, requested_by="operator"
        )
        env.service.approve_repair(approval.id, approved_by="admin")
        updated = env.service.repair_seat(
            "cls-001", "seat-s01", row=None, column=None, approved_by="admin"
        )

        assert updated.row is None
        assert updated.column is None

    def test_repair_rejects_occupied_coordinate(self) -> None:
        """미사용 양수 좌표만 허용 — 다른 좌석이 예약한 좌표는 거부한다."""
        env = _build_env()
        env.create_seat(_seat("seat-s01", "S01", row=1, column=1))
        env.create_seat(_seat("seat-s02", "S02"))

        approval = env.service.request_repair(
            "cls-001", "seat-s02", row=1, column=1, requested_by="operator"
        )
        env.service.approve_repair(approval.id, approved_by="admin")

        with pytest.raises(SeatRepairInvalidError):
            env.service.repair_seat("cls-001", "seat-s02", row=1, column=1, approved_by="admin")

    def test_repair_rejects_coordinates_different_from_approval(self) -> None:
        env = _build_env()
        env.create_seat(_seat("seat-s01", "S01"))

        approval = env.service.request_repair(
            "cls-001", "seat-s01", row=5, column=1, requested_by="operator"
        )
        env.service.approve_repair(approval.id, approved_by="admin")

        with pytest.raises(SeatRepairNotApprovedError):
            env.service.repair_seat("cls-001", "seat-s01", row=6, column=1, approved_by="admin")

    def test_repair_rejects_wrong_approver(self) -> None:
        """승인자와 repair 실행자가 달라도 거부한다."""
        env = _build_env()
        env.create_seat(_seat("seat-s01", "S01"))

        approval = env.service.request_repair(
            "cls-001", "seat-s01", row=5, column=1, requested_by="operator"
        )
        env.service.approve_repair(approval.id, approved_by="admin")

        with pytest.raises(SeatRepairNotApprovedError):
            env.service.repair_seat(
                "cls-001", "seat-s01", row=5, column=1, approved_by="someone-else"
            )

    def test_repair_approval_audit_records_seat_id(self) -> None:
        """승인 기록에 seat ID가 남아 seat별 감사가 가능하다."""
        env = _build_env()
        env.create_seat(_seat("seat-s01", "S01"))
        env.create_seat(_seat("seat-s02", "S02"))

        first = env.service.request_repair(
            "cls-001", "seat-s01", row=5, column=1, requested_by="op-a"
        )
        second = env.service.request_repair(
            "cls-001", "seat-s02", row=6, column=1, requested_by="op-b"
        )
        env.service.approve_repair(first.id, approved_by="admin-a")
        env.service.approve_repair(second.id, approved_by="admin-b")

        approvals = env.migration_repo.list_approvals("cls-001")
        assert {approval.seat_id for approval in approvals} == {"seat-s01", "seat-s02"}
        for approval in approvals:
            assert approval.status == RepairApprovalStatus.APPROVED
            assert approval.approved_by is not None


# ============================================================
# mongo migration 저장소 (fake)
# ============================================================


class FakeCollection:
    def __init__(self, documents: list[dict[str, object]] | None = None) -> None:
        self.documents: list[dict[str, object]] = list(documents or [])
        self.indexes: list[tuple[list[tuple[str, int]], dict[str, object]]] = []

    def create_index(self, fields: list[tuple[str, int]], **options: object) -> None:
        self.indexes.append((fields, options))

    def find_one(
        self, query: dict[str, object], sort: list[tuple[str, int]] | None = None
    ) -> dict[str, object] | None:
        matching = [
            document
            for document in self.documents
            if all(document.get(key) == value for key, value in query.items())
        ]
        if sort is not None:
            for key, direction in sort:
                matching.sort(
                    key=lambda document: cast(Any, document[key]),
                    reverse=direction == -1,
                )
        return matching[0] if matching else None

    def insert_one(self, document: dict[str, object]) -> None:
        self.documents.append(document)

    def replace_one(
        self,
        query: dict[str, object],
        replacement: dict[str, object],
        upsert: bool = False,
    ) -> None:
        index = next(
            (
                i
                for i, document in enumerate(self.documents)
                if all(document.get(key) == value for key, value in query.items())
            ),
            None,
        )
        if index is None:
            if upsert:
                self.documents.append(replacement)
            return
        self.documents[index] = replacement

    def update_one(self, query: dict[str, object], update: dict[str, object]) -> None:
        set_values = update.get("$set")
        if not isinstance(set_values, dict):
            raise TypeError
        for i, document in enumerate(self.documents):
            if all(document.get(key) == value for key, value in query.items()):
                self.documents[i] = {**document, **set_values}
                return

    def find(self, query: dict[str, object], sort: object = None) -> FakeCursor:
        del sort
        return FakeCursor(
            [
                document
                for document in self.documents
                if all(document.get(key) == value for key, value in query.items())
            ]
        )


class FakeCursor:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self._documents = documents

    def sort(self, key: object) -> FakeCursor:
        del key
        return self

    def __iter__(self) -> Iterator[dict[str, object]]:
        return iter(self._documents)


class FakeDatabase:
    def __init__(self) -> None:
        self._collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self._collections.setdefault(name, FakeCollection())

    def __setitem__(self, name: str, collection: FakeCollection) -> None:
        self._collections[name] = collection


class TestMongoMigrationRepository:
    def _build(self) -> tuple[MongoSeatMigrationRepository, FakeDatabase]:
        database = FakeDatabase()
        repository = MongoSeatMigrationRepository(database)  # type: ignore[arg-type]
        return repository, database

    def _snapshot(self, name: str, *, created_at: datetime = _FIXED_NOW) -> SeatMigrationSnapshot:
        return SeatMigrationSnapshot(
            id=f"snap-{name}",
            classroom_id="cls-001",
            name=name,
            seats_count=1,
            assignments_count=0,
            checksum="a" * 64,
            created_at=created_at,
            expires_at=created_at,
            restored_at=None,
        )

    def test_snapshot_and_payload_roundtrip(self) -> None:
        repository, _ = self._build()
        snapshot = self._snapshot("backup")
        seat = _seat("seat-s01", "S01", row=1, column=1)

        repository.save_snapshot(snapshot, seats=(seat,), assignments=())

        assert repository.get_snapshot(snapshot.id) == snapshot
        payload = repository.get_snapshot_payload(snapshot.id)
        assert payload is not None
        assert payload.seats == (seat,)
        assert payload.assignments == ()

    def test_latest_snapshot_returns_newest(self) -> None:
        repository, _ = self._build()
        older = self._snapshot("older", created_at=_FIXED_NOW)
        newer = self._snapshot("newer", created_at=datetime(2026, 8, 14, 10, 0, tzinfo=UTC))

        repository.save_snapshot(older, seats=(), assignments=())
        repository.save_snapshot(newer, seats=(), assignments=())

        latest = repository.latest_snapshot("cls-001")
        assert latest is not None
        assert latest.id == newer.id

    def test_records_and_approvals_roundtrip(self) -> None:
        repository, _ = self._build()
        record = SeatMigrationRecord(
            id="rec-1",
            classroom_id="cls-001",
            seat_id="seat-s01",
            action=SeatMigrationAction.APPEND,
            previous_row=None,
            previous_column=None,
            new_row=1,
            new_column=1,
            created_at=_FIXED_NOW,
            snapshot_id="snap-1",
        )
        approval = RepairApproval(
            id="apr-1",
            classroom_id="cls-001",
            seat_id="seat-s01",
            requested_row=5,
            requested_column=1,
            requested_by="operator",
            requested_at=_FIXED_NOW,
            status=RepairApprovalStatus.APPROVED,
            approved_by="admin",
            approved_at=_FIXED_NOW,
        )

        assert repository.save_record(record) == record
        assert repository.list_records("cls-001") == [record]
        assert repository.save_approval(approval) == approval
        assert repository.get_approval(approval.id) == approval
        approved = repository.approved_approval_for_seat("cls-001", "seat-s01")
        assert approved is not None
        assert approved.approved_by == "admin"

    def test_ensure_indexes_creates_lookup_indexes(self) -> None:
        database = FakeDatabase()
        MongoSeatMigrationRepository.ensure_indexes(database)  # type: ignore[arg-type]

        assert set(database._collections) == {
            MongoSeatMigrationRepository.snapshot_collection_name,
            MongoSeatMigrationRepository.record_collection_name,
            MongoSeatMigrationRepository.approval_collection_name,
        }
        snapshot_indexes = database._collections[
            MongoSeatMigrationRepository.snapshot_collection_name
        ].indexes
        assert any(
            fields == [("classroom_id", 1), ("created_at", -1)] for fields, _ in snapshot_indexes
        )


# ============================================================
# 라우터 (API)
# ============================================================


@dataclass
class RouterEnv:
    client: TestClient
    store: InMemoryClassroomRepository
    service: SeatMigrationService


@pytest.fixture
def env() -> Iterator[RouterEnv]:
    store = InMemoryClassroomRepository()
    store.create_classroom(
        Classroom(
            id="cls-001",
            code="R101",
            name="강의실1",
            location="본관",
            is_active=True,
            created_at=_FIXED_NOW,
        )
    )
    assignment_repo = InMemorySeatAssignmentRepository(store=store)
    migration_repo = InMemorySeatMigrationRepository()
    service = SeatMigrationService(
        store,
        migration_repository=migration_repo,
        assignment_repository=assignment_repo,
        cutover_ready=lambda: True,
        clock=_clock,
    )
    app.dependency_overrides[get_seat_migration_service] = lambda: service
    try:
        yield RouterEnv(client=TestClient(app), store=store, service=service)
    finally:
        app.dependency_overrides.clear()


class TestMigrationRouter:
    def test_preflight_endpoint(self, env: RouterEnv) -> None:
        env.store.create_seat(_seat("seat-s01", "S01", row=1, column=1))
        env.store.create_seat(_seat("seat-s02", "S02"))

        response = env.client.post("/api/v1/classrooms/cls-001/seats/migration/preflight")

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["completed_count"] == 1
        assert body["active_null_count"] == 1

    def test_preflight_endpoint_reports_partial(self, env: RouterEnv) -> None:
        env.store.create_seat(_seat("seat-s01", "S01", row=1))

        response = env.client.post("/api/v1/classrooms/cls-001/seats/migration/preflight")

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert body["partial_coordinates_count"] == 1
        assert body["blocked_reason"] is not None

    def test_run_endpoint_appends_and_returns_snapshot(self, env: RouterEnv) -> None:
        env.store.create_seat(_seat("seat-s01", "S01", row=1, column=1))
        env.store.create_seat(_seat("seat-s02", "S02"))

        response = env.client.post("/api/v1/classrooms/cls-001/seats/migration/run")

        assert response.status_code == 200
        body = response.json()
        assert body["migrated_count"] == 1
        assert body["snapshot"]["seats_count"] == 2
        assert len(body["records"]) == 1
        assert body["records"][0]["action"] == "APPEND"

    def test_run_endpoint_aborts_on_partial(self, env: RouterEnv) -> None:
        env.store.create_seat(_seat("seat-s01", "S01", row=1))

        response = env.client.post("/api/v1/classrooms/cls-001/seats/migration/run")

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "SEAT_MIGRATION_PREFLIGHT_FAILED"

    def test_run_endpoint_blocked_without_kms(self) -> None:
        store = InMemoryClassroomRepository()
        store.create_classroom(
            Classroom(
                id="cls-001",
                code="R101",
                name="강의실1",
                location="본관",
                is_active=True,
                created_at=_FIXED_NOW,
            )
        )
        service = SeatMigrationService(
            store,
            migration_repository=InMemorySeatMigrationRepository(),
            assignment_repository=InMemorySeatAssignmentRepository(store=store),
            cutover_ready=lambda: False,
            clock=_clock,
        )
        app.dependency_overrides[get_seat_migration_service] = lambda: service
        try:
            client = TestClient(app)
            response = client.post("/api/v1/classrooms/cls-001/seats/migration/run")
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "SEAT_MIGRATION_BLOCKED"

    def test_rollback_endpoint_restores(self, env: RouterEnv) -> None:
        env.store.create_seat(_seat("seat-s01", "S01"))

        run_response = env.client.post("/api/v1/classrooms/cls-001/seats/migration/run")
        assert run_response.status_code == 200
        snapshot_id = run_response.json()["snapshot"]["id"]
        assert env.store.get_seat("seat-s01") is not None
        assert env.store.get_seat("seat-s01").row == 1  # type: ignore[union-attr]

        response = env.client.post("/api/v1/classrooms/cls-001/seats/migration/rollback")

        assert response.status_code == 200
        body = response.json()
        assert body["snapshot"]["id"] == snapshot_id
        assert env.store.get_seat("seat-s01").row is None  # type: ignore[union-attr]

    def test_rollback_endpoint_with_named_snapshot(self, env: RouterEnv) -> None:
        env.store.create_seat(_seat("seat-s01", "S01"))
        snapshot = env.service.create_snapshot("cls-001", name="manual-backup")

        env.service.run_migration("cls-001")
        response = env.client.post(
            "/api/v1/classrooms/cls-001/seats/migration/rollback",
            json={"snapshot_id": snapshot.id},
        )

        assert response.status_code == 200
        assert response.json()["snapshot"]["id"] == snapshot.id
        assert env.store.get_seat("seat-s01").row is None  # type: ignore[union-attr]

    def test_status_endpoint_contains_snapshot_without_pii(self, env: RouterEnv) -> None:
        env.store.create_seat(_seat("seat-s01", "S01", row=1, column=1))
        env.store.create_seat(_seat("seat-s02", "S02", label="학생 이름 자리"))
        env.service.run_migration("cls-001")

        response = env.client.get("/api/v1/classrooms/cls-001/seats/migration/status")

        assert response.status_code == 200
        body = response.json()
        assert body["preflight"]["ok"] is True
        assert body["validation"]["ok"] is True
        assert len(body["records"]) == 1
        snapshot = body["snapshot"]
        assert snapshot is not None
        snapshot_json = json.dumps(snapshot)
        assert "seat-s01" not in snapshot_json
        assert "seat-s02" not in snapshot_json
        assert "학생 이름" not in snapshot_json
        assert "archive" not in snapshot_json

    def test_status_endpoint_unknown_classroom_404(self, env: RouterEnv) -> None:
        response = env.client.get("/api/v1/classrooms/missing/seats/migration/status")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "CLASSROOM_NOT_FOUND"

    def test_repair_request_endpoint_creates_pending_approval(self, env: RouterEnv) -> None:
        env.store.create_seat(_seat("seat-s01", "S01"))

        response = env.client.post(
            "/api/v1/classrooms/cls-001/seats/migration/repair/request",
            json={
                "seat_id": "seat-s01",
                "row": 5,
                "column": 1,
                "requested_by": "operator",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["seat_id"] == "seat-s01"
        assert body["requested_row"] == 5
        assert body["requested_column"] == 1
        assert body["requested_by"] == "operator"
        assert body["status"] == "PENDING"
        assert body["approved_by"] is None
        assert body["approved_at"] is None

    def test_repair_request_endpoint_rejects_partial(self, env: RouterEnv) -> None:
        """행만 지정한 부분 입력은 서비스 계층이 SEAT_REPAIR_INVALID로 거부한다."""
        env.store.create_seat(_seat("seat-s01", "S01"))

        response = env.client.post(
            "/api/v1/classrooms/cls-001/seats/migration/repair/request",
            json={"seat_id": "seat-s01", "row": 5, "column": None, "requested_by": "operator"},
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "SEAT_REPAIR_INVALID"

    def test_repair_approve_endpoint_approves(self, env: RouterEnv) -> None:
        env.store.create_seat(_seat("seat-s01", "S01"))
        approval = env.service.request_repair(
            "cls-001", "seat-s01", row=5, column=1, requested_by="operator"
        )

        response = env.client.post(
            "/api/v1/classrooms/cls-001/seats/migration/repair/approve",
            json={"approval_id": approval.id, "approved_by": "admin"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == approval.id
        assert body["status"] == "APPROVED"
        assert body["approved_by"] == "admin"
        assert body["approved_at"] is not None

    def test_repair_approve_endpoint_rejects_unknown_approval(self, env: RouterEnv) -> None:
        response = env.client.post(
            "/api/v1/classrooms/cls-001/seats/migration/repair/approve",
            json={"approval_id": "missing", "approved_by": "admin"},
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "SEAT_REPAIR_NOT_APPROVED"

    def test_repair_execute_endpoint_applies_approved_repair(self, env: RouterEnv) -> None:
        env.store.create_seat(_seat("seat-s01", "S01"))
        approval = env.service.request_repair(
            "cls-001", "seat-s01", row=5, column=1, requested_by="operator"
        )
        env.service.approve_repair(approval.id, approved_by="admin")

        response = env.client.post(
            "/api/v1/classrooms/cls-001/seats/migration/repair/execute",
            json={"seat_id": "seat-s01", "row": 5, "column": 1, "approved_by": "admin"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == "seat-s01"
        assert body["row"] == 5
        assert body["column"] == 1

        status_response = env.client.get("/api/v1/classrooms/cls-001/seats/migration/status")
        assert status_response.status_code == 200
        records = status_response.json()["records"]
        assert len(records) == 1
        assert records[0]["action"] == "REPAIR"
        assert records[0]["seat_id"] == "seat-s01"

    def test_repair_execute_endpoint_rejects_without_approval(self, env: RouterEnv) -> None:
        env.store.create_seat(_seat("seat-s01", "S01"))

        response = env.client.post(
            "/api/v1/classrooms/cls-001/seats/migration/repair/execute",
            json={"seat_id": "seat-s01", "row": 5, "column": 1, "approved_by": "admin"},
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "SEAT_REPAIR_NOT_APPROVED"

    def test_repair_execute_endpoint_unset_form_clears_coordinates(self, env: RouterEnv) -> None:
        """양쪽 unset 요청은 좌표를 해제한다."""
        env.store.create_seat(_seat("seat-s01", "S01", row=2, column=2))
        approval = env.service.request_repair(
            "cls-001", "seat-s01", row=None, column=None, requested_by="operator"
        )
        env.service.approve_repair(approval.id, approved_by="admin")

        response = env.client.post(
            "/api/v1/classrooms/cls-001/seats/migration/repair/execute",
            json={"seat_id": "seat-s01", "row": None, "column": None, "approved_by": "admin"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == "seat-s01"
        assert body["row"] is None
        assert body["column"] is None


# ============================================================
# checksum 헬퍼 직접 검증
# ============================================================


class TestChecksumHelper:
    def test_checksum_is_sha256_hex(self) -> None:
        checksum = _snapshot_checksum(
            [_seat("seat-s01", "S01", row=1, column=1)],
            [SeatAssignment("seat-s01", "stu-001", "cls-001", _FIXED_NOW)],
        )
        assert len(checksum) == 64
        assert int(checksum, 16) >= 0

    def test_checksum_differs_when_assignment_changes(self) -> None:
        base = _snapshot_checksum(
            [_seat("seat-s01", "S01", row=1, column=1)],
            [SeatAssignment("seat-s01", "stu-001", "cls-001", _FIXED_NOW)],
        )
        changed = _snapshot_checksum(
            [_seat("seat-s01", "S01", row=1, column=1)],
            [SeatAssignment("seat-s01", "stu-002", "cls-001", _FIXED_NOW)],
        )
        assert base != changed
