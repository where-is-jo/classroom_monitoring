"""TASK-001: 좌석 자동 생성(auto) 계약·allocator·conflict·retry 검증 테스트.

- `POST /{classroom_id}/seats/auto`의 엄격 스키마(extra forbid)·201·Location·label 규칙
- 코드 allocator (`^S(\\d+)$` 파싱, 최소 누락값, 비활성 포함 예약)
- SeatCodeConflict / SeatCoordinateConflict / SeatCodeAllocationConflict 구분
- code conflict에서만 최대 3회 재시도, 좌표 conflict는 즉시 실패
- legacy `POST /seats`의 generic SEAT_DUPLICATE 매핑 유지 (typed conflict는 auto에만)
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import cast

import pytest
from fastapi.testclient import TestClient

from app.classrooms.adapters.memory_repository import InMemoryClassroomRepository
from app.classrooms.errors import (
    SeatCodeAllocationConflictError,
    SeatCodeConflictError,
    SeatCoordinateConflictError,
)
from app.classrooms.models import CreateClassroomCommand
from app.classrooms.service import ClassroomService
from app.main import app
from app.shared.dependencies import get_classroom_service

NOW = datetime(2026, 8, 13, 9, 0, tzinfo=UTC)


def _make_service() -> ClassroomService:
    return ClassroomService(
        InMemoryClassroomRepository(),
        occupancy_confidence_threshold=0.5,
        clock=lambda: NOW,
    )


@pytest.fixture
def service() -> Iterator[ClassroomService]:
    instance = _make_service()
    app.dependency_overrides[get_classroom_service] = lambda: instance
    try:
        yield instance
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def client(service: ClassroomService) -> Iterator[TestClient]:
    del service
    yield TestClient(app)


def _seed_classroom(service: ClassroomService) -> str:
    classroom = service.seed_classroom(
        CreateClassroomCommand(
            id="classroom-a101",
            code="A101",
            name="일반 강의실",
            location="A동 1층",
        )
    )
    return classroom.id


def _create_classroom(client: TestClient) -> str:
    response = client.post(
        "/api/v1/classrooms",
        json={"code": "A101", "name": "일반 강의실", "location": "A동 1층"},
    )
    assert response.status_code == 201
    return str(cast(dict[str, object], response.json())["id"])


def _create_legacy_seat(
    client: TestClient,
    classroom_id: str,
    *,
    code: str,
    label: str,
    row: int | None = None,
    column: int | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {"code": code, "label": label}
    if row is not None:
        payload["row"] = row
    if column is not None:
        payload["column"] = column
    response = client.post(f"/api/v1/classrooms/{classroom_id}/seats", json=payload)
    assert response.status_code == 201
    return cast(dict[str, object], response.json())


# ============================================================
# auto 좌석 생성 API 계약
# ============================================================


class TestAutoSeatCreateApi:
    def test_auto_create_returns_201_location_and_label(self, client: TestClient) -> None:
        classroom_id = _create_classroom(client)

        response = client.post(
            f"/api/v1/classrooms/{classroom_id}/seats/auto",
            json={"row": 1, "column": 1},
        )

        assert response.status_code == 201
        body = response.json()
        seat_id = str(body["id"])
        assert body["code"] == "S01"
        assert body["label"] == "좌석 S01"
        assert body["row"] == 1
        assert body["column"] == 1
        assert body["is_active"] is True
        assert response.headers["location"] == (
            f"/api/v1/classrooms/{classroom_id}/seats/{seat_id}"
        )

    def test_auto_create_rejects_extra_field(self, client: TestClient) -> None:
        """AutoSeatCreateRequest는 extra forbid — 정의되지 않은 필드는 422다."""
        classroom_id = _create_classroom(client)

        response = client.post(
            f"/api/v1/classrooms/{classroom_id}/seats/auto",
            json={"row": 1, "column": 1, "label": "추가 필드"},
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_auto_create_requires_row_and_column(self, client: TestClient) -> None:
        classroom_id = _create_classroom(client)

        missing_row = client.post(
            f"/api/v1/classrooms/{classroom_id}/seats/auto",
            json={"column": 1},
        )
        assert missing_row.status_code == 422

        missing_column = client.post(
            f"/api/v1/classrooms/{classroom_id}/seats/auto",
            json={"row": 1},
        )
        assert missing_column.status_code == 422

        empty = client.post(f"/api/v1/classrooms/{classroom_id}/seats/auto", json={})
        assert empty.status_code == 422

    @pytest.mark.parametrize(("row", "column"), [(0, 1), (1, 0), (-1, 2), (1, -2)])
    def test_auto_create_rejects_row_column_below_one(
        self, client: TestClient, row: int, column: int
    ) -> None:
        classroom_id = _create_classroom(client)

        response = client.post(
            f"/api/v1/classrooms/{classroom_id}/seats/auto",
            json={"row": row, "column": column},
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_auto_create_in_unknown_classroom_returns_404(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/classrooms/missing/seats/auto",
            json={"row": 1, "column": 1},
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "CLASSROOM_NOT_FOUND"


# ============================================================
# 코드 allocator
# ============================================================


class TestSeatCodeAllocator:
    def test_first_auto_code_is_s01(self) -> None:
        service = _make_service()
        classroom_id = _seed_classroom(service)

        seat = service.create_seat_auto(classroom_id, row=1, column=1)

        assert seat.code == "S01"
        assert seat.label == "좌석 S01"

    def test_auto_code_picks_minimal_missing(self) -> None:
        """S01, S03이 예약된 상태에서 최소 누락값은 S02다."""
        service = _make_service()
        classroom_id = _seed_classroom(service)
        service.create_seat(classroom_id, code="S01", label="좌석 1", row=1, column=1)
        service.create_seat(classroom_id, code="S03", label="좌석 3", row=1, column=2)

        seat = service.create_seat_auto(classroom_id, row=1, column=3)

        assert seat.code == "S02"

    def test_auto_code_after_s99_is_s100(self) -> None:
        """S01~S99가 모두 예약되면 S100(자연수 표기)이 된다."""
        service = _make_service()
        classroom_id = _seed_classroom(service)
        for number in range(1, 100):
            service.create_seat(
                classroom_id,
                code=f"S{number:02d}",
                label=f"좌석 {number}",
                row=number,
                column=1,
            )

        seat = service.create_seat_auto(classroom_id, row=100, column=1)

        assert seat.code == "S100"

    def test_inactive_seat_reserves_code(self) -> None:
        """legacy inactive(PUT is_active=false) 좌석도 코드를 예약한다.

        hard delete로 지운 좌석과 달리 레코드가 남아 있으므로 코드가 해제되지
        않는다 (SPEC: 활성·legacy inactive 레코드는 code를 예약한다).
        """
        service = _make_service()
        classroom_id = _seed_classroom(service)
        reserved = service.create_seat(
            classroom_id, code="S01", label="좌석 1", row=1, column=1
        )
        service.update_seat(reserved.id, is_active=False)

        seat = service.create_seat_auto(classroom_id, row=1, column=2)

        assert seat.code == "S02"

    def test_hard_deleted_seat_releases_code(self) -> None:
        """hard delete된 좌석의 코드는 즉시 해제되어 재사용된다."""
        service = _make_service()
        classroom_id = _seed_classroom(service)
        reserved = service.create_seat(
            classroom_id, code="S01", label="좌석 1", row=1, column=1
        )
        service.delete_seat(reserved.id)

        seat = service.create_seat_auto(classroom_id, row=1, column=2)

        assert seat.code == "S01"
        assert seat.id != reserved.id

    def test_allocator_ignores_non_matching_codes(self) -> None:
        """``^S(\\d+)$``가 아닌 코드는 예약 대상에서 제외된다."""
        service = _make_service()
        classroom_id = _seed_classroom(service)
        service.create_seat(classroom_id, code="A01", label="좌석 A", row=1, column=1)
        service.create_seat(classroom_id, code="S01", label="좌석 1", row=1, column=2)

        seat = service.create_seat_auto(classroom_id, row=1, column=3)

        assert seat.code == "S02"

    def test_multiple_auto_creates_increment_codes(self) -> None:
        service = _make_service()
        classroom_id = _seed_classroom(service)

        first = service.create_seat_auto(classroom_id, row=1, column=1)
        second = service.create_seat_auto(classroom_id, row=1, column=2)

        assert first.code == "S01"
        assert second.code == "S02"


# ============================================================
# conflict 유형 구분 (typed conflict는 auto에만 노출)
# ============================================================


class TestConflictTypes:
    def test_auto_coordinate_conflict_returns_409_coordinate_conflict(
        self, client: TestClient
    ) -> None:
        classroom_id = _create_classroom(client)
        _create_legacy_seat(client, classroom_id, code="S01", label="좌석 1", row=1, column=1)

        response = client.post(
            f"/api/v1/classrooms/{classroom_id}/seats/auto",
            json={"row": 1, "column": 1},
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "SEAT_COORDINATE_CONFLICT"

    def test_auto_coordinate_conflict_with_inactive_seat(self) -> None:
        """legacy inactive 좌석도 좌표를 예약하므로 auto에서 좌표 conflict가 된다."""
        service = _make_service()
        classroom_id = _seed_classroom(service)
        seat = service.create_seat(classroom_id, code="S01", label="좌석 1", row=1, column=1)
        service.update_seat(seat.id, is_active=False)

        with pytest.raises(SeatCoordinateConflictError) as exc_info:
            service.create_seat_auto(classroom_id, row=1, column=1)

        assert exc_info.value.code == "SEAT_COORDINATE_CONFLICT"
        assert exc_info.value.status_code == 409

    def test_auto_no_coordinate_conflict_after_hard_delete(self) -> None:
        """hard delete된 좌석의 좌표는 해제되어 같은 좌표로 새 좌석을 만들 수 있다."""
        service = _make_service()
        classroom_id = _seed_classroom(service)
        seat = service.create_seat(classroom_id, code="S01", label="좌석 1", row=1, column=1)
        service.delete_seat(seat.id)

        created = service.create_seat_auto(classroom_id, row=1, column=1)

        assert (created.row, created.column) == (1, 1)
        assert created.id != seat.id

    def test_code_conflict_classified_as_seat_code_conflict(self, service: ClassroomService) -> None:
        """예약된 코드를 할당하면 code conflict로 분류된다 (재시도 대상)."""
        classroom_id = _seed_classroom(service)
        service.create_seat(classroom_id, code="S01", label="좌석 1", row=1, column=1)
        service._allocate_seat_code = lambda _classroom_id: "S01"  # type: ignore[assignment]

        with pytest.raises(SeatCodeConflictError) as exc_info:
            service._create_auto_seat(classroom_id, row=2, column=2)

        assert exc_info.value.code == "SEAT_CODE_CONFLICT"
        assert exc_info.value.status_code == 409

    def test_coordinate_conflict_priority_over_code_conflict(
        self, service: ClassroomService,
    ) -> None:
        """코드·좌표가 동시에 겹쳐도 좌표 conflict가 우선 노출된다 (재시도 없음)."""
        classroom_id = _seed_classroom(service)
        service.create_seat(classroom_id, code="S01", label="좌석 1", row=1, column=1)
        service._allocate_seat_code = lambda _classroom_id: "S01"  # type: ignore[assignment]

        with pytest.raises(SeatCoordinateConflictError):
            service.create_seat_auto(classroom_id, row=1, column=1)

    def test_legacy_endpoint_keeps_generic_seat_duplicate(self, client: TestClient) -> None:
        """legacy POST /seats는 typed conflict 대신 generic SEAT_DUPLICATE를 유지한다."""
        classroom_id = _create_classroom(client)
        _create_legacy_seat(client, classroom_id, code="S01", label="좌석 1")

        response = client.post(
            f"/api/v1/classrooms/{classroom_id}/seats",
            json={"code": "S01", "label": "중복 좌석"},
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "SEAT_DUPLICATE"

    def test_legacy_coordinate_duplicate_keeps_generic_seat_duplicate(
        self, client: TestClient,
    ) -> None:
        """legacy POST /seats의 좌표 중복도 generic SEAT_DUPLICATE로 유지한다."""
        classroom_id = _create_classroom(client)
        _create_legacy_seat(client, classroom_id, code="S01", label="좌석 1", row=1, column=1)

        response = client.post(
            f"/api/v1/classrooms/{classroom_id}/seats",
            json={"code": "S02", "label": "중복 좌표", "row": 1, "column": 1},
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "SEAT_DUPLICATE"


# ============================================================
# code conflict 재시도 (최대 3회, 소진 시 SEAT_CODE_ALLOCATION_CONFLICT)
# ============================================================


class TestAutoRetryLogic:
    def test_transient_code_conflict_retries_then_succeeds(self, service: ClassroomService) -> None:
        """일시적 code conflict는 재시도로 해소되어 정상 생성된다."""
        classroom_id = _seed_classroom(service)
        service.create_seat(classroom_id, code="S01", label="좌석 1", row=1, column=1)
        calls = {"count": 0}
        original = service._allocate_seat_code

        def flaky_allocate(_classroom_id: str) -> str:
            calls["count"] += 1
            if calls["count"] == 1:
                return "S01"  # 첫 시도는 이미 예약된 코드 (code conflict)
            return original(_classroom_id)  # 이후는 실제 allocator

        service._allocate_seat_code = flaky_allocate  # type: ignore[assignment]

        seat = service.create_seat_auto(classroom_id, row=2, column=2)

        assert seat.code == "S02"
        assert calls["count"] == 2

    def test_code_conflict_retries_three_times_then_fails(self, service: ClassroomService) -> None:
        """code conflict가 3회 지속되면 최종 SEAT_CODE_ALLOCATION_CONFLICT를 던진다."""
        classroom_id = _seed_classroom(service)
        service.create_seat(classroom_id, code="S01", label="좌석 1", row=1, column=1)
        calls = {"count": 0}

        def always_taken(_classroom_id: str) -> str:
            calls["count"] += 1
            return "S01"

        service._allocate_seat_code = always_taken  # type: ignore[assignment]

        with pytest.raises(SeatCodeAllocationConflictError) as exc_info:
            service.create_seat_auto(classroom_id, row=2, column=2)

        assert exc_info.value.code == "SEAT_CODE_ALLOCATION_CONFLICT"
        assert exc_info.value.status_code == 409
        assert calls["count"] == 3

    def test_code_conflict_exhaustion_returns_409_allocation_conflict(
        self, client: TestClient, service: ClassroomService,
    ) -> None:
        """API 레벨: 재시도 소진 시 409 SEAT_CODE_ALLOCATION_CONFLICT다."""
        classroom_id = _create_classroom(client)
        _create_legacy_seat(client, classroom_id, code="S01", label="좌석 1", row=1, column=1)
        service._allocate_seat_code = lambda _classroom_id: "S01"  # type: ignore[assignment]

        response = client.post(
            f"/api/v1/classrooms/{classroom_id}/seats/auto",
            json={"row": 2, "column": 2},
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "SEAT_CODE_ALLOCATION_CONFLICT"


# ============================================================
# typed conflict 오류 매핑 (409 + error envelope)
# ============================================================


class TestTypedConflictErrorMapping:
    def test_seat_code_conflict_error_maps_to_409(self) -> None:
        error = SeatCodeConflictError()
        assert error.code == "SEAT_CODE_CONFLICT"
        assert error.status_code == 409

    def test_seat_coordinate_conflict_error_maps_to_409(self) -> None:
        error = SeatCoordinateConflictError()
        assert error.code == "SEAT_COORDINATE_CONFLICT"
        assert error.status_code == 409

    def test_seat_code_allocation_conflict_error_maps_to_409(self) -> None:
        error = SeatCodeAllocationConflictError()
        assert error.code == "SEAT_CODE_ALLOCATION_CONFLICT"
        assert error.status_code == 409
