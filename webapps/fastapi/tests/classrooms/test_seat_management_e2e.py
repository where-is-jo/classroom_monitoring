"""TASK-005: 좌석 관리 통합 워크플로 종단(end-to-end) 시나리오.

기존 단위/기능 테스트(test_seat_auto_create, test_uow, test_migration,
test_seat_hard_delete 등)는 기능별로 쪼개져 있어 "여러 기능이 순서대로
이어질 때"의 문제(migration 이후 hard delete 조합, hard delete 이후
재생성 시 새 UUID·코드 재사용 등)를 커버하지 못한다. 이 모듈은 실제
HTTP API(TestClient)를 통해 하나의 연속 플로우를 검증한다:

    강의실 생성 → legacy/auto 좌석 생성(allocator 공유 확인) →
    학생 배정·이동 → migration preflight/run/재실행(idempotent skip) →
    migration 이후 좌석의 hard delete → 반복/후속 mutation 404 →
    같은 좌표·코드 재사용으로 재생성 시 새 UUID

각 단계마다 실제 응답 코드·바디와 이어지는 상태(좌석 목록, 배정 현황,
migration 상태)를 확인해 회귀를 방지한다.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import cast

import pytest
from fastapi.testclient import TestClient

from app.classrooms.adapters.memory_repository import (
    InMemoryClassroomRepository,
    InMemorySeatAssignmentRepository,
    InMemorySeatMigrationRepository,
    InMemorySeatMutationUnitOfWork,
)
from app.classrooms.migration import SeatMigrationService
from app.classrooms.service import ClassroomService
from app.main import app
from app.shared.adapters.memory_student_lookup import InMemoryStudentLookup
from app.shared.dependencies import get_classroom_service, get_seat_migration_service
from app.shared.student_identity import StudentIdentity

_NOW = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)


def _fixed_now() -> datetime:
    return _NOW


@pytest.fixture
def client() -> Iterator[TestClient]:
    """classroom/migration 서비스가 같은 store를 공유하는 실제 HTTP client.

    두 서비스가 같은 ``InMemoryClassroomRepository``·assignment repository를
    공유해야 seat 생성/배정과 migration이 하나의 연속 플로우로 이어진다.
    """
    store = InMemoryClassroomRepository()
    assignment_repo = InMemorySeatAssignmentRepository(store=store)
    uow = InMemorySeatMutationUnitOfWork(store, clock=_fixed_now)
    student_lookup = InMemoryStudentLookup(
        identities=(
            StudentIdentity(id="stu-001", student_no="20260101", name="홍길동", is_active=True),
            StudentIdentity(id="stu-002", student_no="20260102", name="김철수", is_active=True),
        )
    )
    classroom_service = ClassroomService(
        store,
        student_lookup=student_lookup,
        assignment_repository=assignment_repo,
        uow=uow,
        occupancy_confidence_threshold=0.6,
        clock=_fixed_now,
    )
    migration_service = SeatMigrationService(
        store,
        migration_repository=InMemorySeatMigrationRepository(),
        assignment_repository=assignment_repo,
        cutover_ready=lambda: True,
        clock=_fixed_now,
    )
    app.dependency_overrides[get_classroom_service] = lambda: classroom_service
    app.dependency_overrides[get_seat_migration_service] = lambda: migration_service
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


class TestSeatManagementEndToEndFlow:
    def test_classroom_to_hard_delete_continuous_flow(self, client: TestClient) -> None:
        # ------------------------------------------------------------
        # 1. 강의실 생성
        # ------------------------------------------------------------
        created_classroom = client.post(
            "/api/v1/classrooms",
            json={"code": "A101", "name": "일반 강의실", "location": "A동 1층"},
        )
        assert created_classroom.status_code == 201
        classroom_id = str(cast(dict[str, object], created_classroom.json())["id"])

        # ------------------------------------------------------------
        # 2. legacy 좌석 생성 (좌표 없음) — allocator가 legacy 코드도 인식해야 한다.
        # ------------------------------------------------------------
        legacy = client.post(
            f"/api/v1/classrooms/{classroom_id}/seats",
            json={"code": "S01", "label": "레거시 좌석"},
        )
        assert legacy.status_code == 201
        legacy_body = legacy.json()
        assert legacy_body["code"] == "S01"
        assert legacy_body["row"] is None
        assert legacy_body["column"] is None
        assert legacy_body["is_active"] is True
        seat_s01_id = str(legacy_body["id"])

        # ------------------------------------------------------------
        # 3. auto 좌석 여러 개 생성 — S01이 이미 예약했으므로 S02, S03을 받아야 한다.
        # ------------------------------------------------------------
        auto_a = client.post(
            f"/api/v1/classrooms/{classroom_id}/seats/auto",
            json={"row": 1, "column": 1},
        )
        assert auto_a.status_code == 201
        auto_a_body = auto_a.json()
        assert auto_a_body["code"] == "S02"
        assert auto_a_body["label"] == "좌석 S02"
        assert auto_a.headers["location"] == (
            f"/api/v1/classrooms/{classroom_id}/seats/{auto_a_body['id']}"
        )
        seat_s02_id = str(auto_a_body["id"])

        auto_b = client.post(
            f"/api/v1/classrooms/{classroom_id}/seats/auto",
            json={"row": 1, "column": 2},
        )
        assert auto_b.status_code == 201
        auto_b_body = auto_b.json()
        assert auto_b_body["code"] == "S03"
        seat_s03_id = str(auto_b_body["id"])

        # 목록에서 세 좌석·좌표가 정확히 이어져야 한다.
        listed = client.get(f"/api/v1/classrooms/{classroom_id}/seats")
        assert listed.status_code == 200
        listed_body = listed.json()
        assert listed_body["total"] == 3
        by_id = {item["id"]: item for item in listed_body["items"]}
        assert by_id[seat_s01_id]["row"] is None
        assert (by_id[seat_s02_id]["row"], by_id[seat_s02_id]["column"]) == (1, 1)
        assert (by_id[seat_s03_id]["row"], by_id[seat_s03_id]["column"]) == (1, 2)

        # ------------------------------------------------------------
        # 4. 학생 배정: S01 → stu-001, S02 → stu-002
        # ------------------------------------------------------------
        assign_s01 = client.put(
            f"/api/v1/classrooms/{classroom_id}/seats/{seat_s01_id}/assignment",
            json={"student_id": "stu-001"},
        )
        assert assign_s01.status_code == 200
        assert assign_s01.json()["student_name"] == "홍길동"

        assign_s02 = client.put(
            f"/api/v1/classrooms/{classroom_id}/seats/{seat_s02_id}/assignment",
            json={"student_id": "stu-002"},
        )
        assert assign_s02.status_code == 200
        assert assign_s02.json()["student_name"] == "김철수"

        # stu-001을 S03으로 이동 — S01 지정은 원자적으로 해제돼야 한다.
        move_to_s03 = client.put(
            f"/api/v1/classrooms/{classroom_id}/seats/{seat_s03_id}/assignment",
            json={"student_id": "stu-001"},
        )
        assert move_to_s03.status_code == 200

        assignments_after_move = client.get(
            f"/api/v1/classrooms/{classroom_id}/seat-assignments"
        )
        assert assignments_after_move.status_code == 200
        by_seat = {
            item["seat_id"]: item["student_id"]
            for item in assignments_after_move.json()["items"]
        }
        assert by_seat == {seat_s02_id: "stu-002", seat_s03_id: "stu-001"}
        assert seat_s01_id not in by_seat

        # ------------------------------------------------------------
        # 5. migration preflight — S01만 활성 null 쌍이어야 한다.
        # ------------------------------------------------------------
        preflight = client.post(
            f"/api/v1/classrooms/{classroom_id}/seats/migration/preflight"
        )
        assert preflight.status_code == 200
        preflight_body = preflight.json()
        assert preflight_body["ok"] is True
        assert preflight_body["total_seats"] == 3
        assert preflight_body["completed_count"] == 2
        assert preflight_body["active_null_count"] == 1
        assert preflight_body["inactive_null_count"] == 0
        assert preflight_body["partial_coordinates_count"] == 0

        # ------------------------------------------------------------
        # 6. migration run — S01에 좌표가 append돼야 한다 (기존 좌표 보존, S02/S03 skip).
        # ------------------------------------------------------------
        run = client.post(f"/api/v1/classrooms/{classroom_id}/seats/migration/run")
        assert run.status_code == 200
        run_body = run.json()
        assert run_body["migrated_count"] == 1
        assert run_body["skipped_with_coordinates_count"] == 2
        assert run_body["inactive_null_skipped_count"] == 0
        assert len(run_body["records"]) == 1
        record = run_body["records"][0]
        assert record["seat_id"] == seat_s01_id
        assert record["action"] == "APPEND"
        assert record["previous_row"] is None
        assert record["new_row"] == 2  # max_row(=1, S02/S03) + ordinal(1)
        assert record["new_column"] == 1
        assert run_body["snapshot"]["seats_count"] == 3
        assert run_body["snapshot"]["assignments_count"] == 2

        migrated_seat = client.get(
            f"/api/v1/classrooms/{classroom_id}/seats/{seat_s01_id}"
        )
        assert migrated_seat.status_code == 200
        assert (migrated_seat.json()["row"], migrated_seat.json()["column"]) == (2, 1)

        status_after_run = client.get(
            f"/api/v1/classrooms/{classroom_id}/seats/migration/status"
        )
        assert status_after_run.status_code == 200
        status_body = status_after_run.json()
        assert status_body["preflight"]["ok"] is True
        assert status_body["preflight"]["active_null_count"] == 0
        assert status_body["snapshot"]["id"] == run_body["snapshot"]["id"]
        assert status_body["validation"]["ok"] is True

        # ------------------------------------------------------------
        # 7. migration 재실행 — 이미 좌표가 있는 행은 모두 건너뛰어야 한다(멱등).
        # ------------------------------------------------------------
        rerun = client.post(f"/api/v1/classrooms/{classroom_id}/seats/migration/run")
        assert rerun.status_code == 200
        rerun_body = rerun.json()
        assert rerun_body["migrated_count"] == 0
        assert rerun_body["skipped_with_coordinates_count"] == 3
        assert rerun_body["records"] == []

        # ------------------------------------------------------------
        # 8. migration된 좌석을 재배정한 뒤 hard delete — 두 기능의 조합을 검증한다.
        # ------------------------------------------------------------
        reassign_s01 = client.put(
            f"/api/v1/classrooms/{classroom_id}/seats/{seat_s01_id}/assignment",
            json={"student_id": "stu-001"},
        )
        assert reassign_s01.status_code == 200

        assignments_before_delete = client.get(
            f"/api/v1/classrooms/{classroom_id}/seat-assignments"
        )
        by_seat_before_delete = {
            item["seat_id"]: item["student_id"]
            for item in assignments_before_delete.json()["items"]
        }
        assert by_seat_before_delete == {seat_s01_id: "stu-001", seat_s02_id: "stu-002"}
        assert seat_s03_id not in by_seat_before_delete  # stu-001이 S03에서 S01로 이동했다.

        delete_response = client.delete(
            f"/api/v1/classrooms/{classroom_id}/seats/{seat_s01_id}"
        )
        assert delete_response.status_code == 204
        assert delete_response.content == b""

        # 좌석·지정이 물리적으로 부재해야 한다.
        deleted_seat = client.get(
            f"/api/v1/classrooms/{classroom_id}/seats/{seat_s01_id}"
        )
        assert deleted_seat.status_code == 404
        assert deleted_seat.json()["error"]["code"] == "SEAT_NOT_FOUND"

        assignments_after_delete = client.get(
            f"/api/v1/classrooms/{classroom_id}/seat-assignments"
        )
        by_seat_after_delete = {
            item["seat_id"]: item["student_id"]
            for item in assignments_after_delete.json()["items"]
        }
        assert by_seat_after_delete == {seat_s02_id: "stu-002"}

        # migration 감사 기록은 history와 마찬가지로 seat 삭제 후에도 보존돼야 한다.
        status_after_delete = client.get(
            f"/api/v1/classrooms/{classroom_id}/seats/migration/status"
        )
        assert status_after_delete.status_code == 200
        status_after_delete_body = status_after_delete.json()
        assert any(
            item["seat_id"] == seat_s01_id for item in status_after_delete_body["records"]
        )
        assert status_after_delete_body["preflight"]["total_seats"] == 2
        assert status_after_delete_body["validation"]["ok"] is True

        # ------------------------------------------------------------
        # 9. 반복·후속 mutation은 기존 404 envelope여야 한다.
        # ------------------------------------------------------------
        repeated_delete = client.delete(
            f"/api/v1/classrooms/{classroom_id}/seats/{seat_s01_id}"
        )
        assert repeated_delete.status_code == 404
        assert repeated_delete.json()["error"]["code"] == "SEAT_NOT_FOUND"

        assign_after_delete = client.put(
            f"/api/v1/classrooms/{classroom_id}/seats/{seat_s01_id}/assignment",
            json={"student_id": "stu-002"},
        )
        assert assign_after_delete.status_code == 404
        assert assign_after_delete.json()["error"]["code"] == "SEAT_NOT_FOUND"

        # ------------------------------------------------------------
        # 10. 같은 코드·좌표로 재생성 — 삭제 후에만 재사용되고 새 UUID를 받아야 한다.
        # ------------------------------------------------------------
        recreated = client.post(
            f"/api/v1/classrooms/{classroom_id}/seats/auto",
            json={"row": 2, "column": 1},  # 삭제된 S01이 예약했던 좌표
        )
        assert recreated.status_code == 201
        recreated_body = recreated.json()
        assert recreated_body["code"] == "S01"  # 코드 슬롯도 삭제 후 재사용된다.
        assert recreated_body["id"] != seat_s01_id  # 새 UUID
        assert (recreated_body["row"], recreated_body["column"]) == (2, 1)

        final_listed = client.get(f"/api/v1/classrooms/{classroom_id}/seats")
        assert final_listed.status_code == 200
        final_body = final_listed.json()
        assert final_body["total"] == 3
        final_ids = {item["id"] for item in final_body["items"]}
        assert final_ids == {recreated_body["id"], seat_s02_id, seat_s03_id}
