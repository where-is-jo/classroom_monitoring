"""좌석 행·열(seat grid) 스키마·서비스·라우터 검증 테스트.

REQ-002(행·열 검증 로직)와 REQ-003(좌석 생성/수정 스키마 변경)을 검증한다.
- 행·열 검증: 1 이상 정수, 부분 입력 불가
- update_seat 해제(unset_row/unset_column)
- SeatResponse에 row/column 매핑
- 라우터에서 row/column 전달
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import cast

import pytest
from fastapi.testclient import TestClient

from app.classrooms.adapters.memory_repository import InMemoryClassroomRepository
from app.classrooms.errors import ClassroomInputError, SeatDuplicateError
from app.classrooms.models import (
    CreateClassroomCommand,
    CreateSeatCommand,
)
from app.classrooms.schemas import SeatCreateRequest, SeatResponse, SeatUpdateRequest
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


# ============================================================
# 행·열 검증 로직 (서비스 단위)
# ============================================================


class TestValidateRowColumn:
    def test_valid_pair(self) -> None:
        service = _make_service()
        service._validate_row_column(1, 1)

    def test_both_none_allowed(self) -> None:
        service = _make_service()
        service._validate_row_column(None, None)

    def test_row_below_one_raises(self) -> None:
        service = _make_service()
        with pytest.raises(ClassroomInputError, match=r"행은 1 이상이어야 합니다."):
            service._validate_row_column(0, 1)

    def test_column_below_one_raises(self) -> None:
        service = _make_service()
        with pytest.raises(ClassroomInputError, match=r"열은 1 이상이어야 합니다."):
            service._validate_row_column(1, 0)

    def test_partial_row_only_raises(self) -> None:
        service = _make_service()
        with pytest.raises(
            ClassroomInputError, match=r"행과 열은 모두 입력하거나 모두 비워야 합니다."
        ):
            service._validate_row_column(1, None)

    def test_partial_column_only_raises(self) -> None:
        service = _make_service()
        with pytest.raises(
            ClassroomInputError, match=r"행과 열은 모두 입력하거나 모두 비워야 합니다."
        ):
            service._validate_row_column(None, 1)


# ============================================================
# create_seat / update_seat (서비스 단위)
# ============================================================


class TestCreateSeatRowColumn:
    def test_creates_seat_with_row_column(self) -> None:
        service = _make_service()
        classroom_id = _seed_classroom(service)

        seat = service.create_seat(classroom_id, code="S01", label="좌석 1", row=1, column=2)

        assert seat.row == 1
        assert seat.column == 2

    def test_creates_seat_without_row_column(self) -> None:
        """행·열 없이 생성해도 동작한다 (하위 호환)."""
        service = _make_service()
        classroom_id = _seed_classroom(service)

        seat = service.create_seat(classroom_id, code="S01", label="좌석 1")

        assert seat.row is None
        assert seat.column is None

    def test_row_below_one_raises(self) -> None:
        service = _make_service()
        classroom_id = _seed_classroom(service)

        with pytest.raises(ClassroomInputError, match=r"행은 1 이상이어야 합니다."):
            service.create_seat(classroom_id, code="S01", label="좌석 1", row=0, column=1)

    def test_partial_input_raises(self) -> None:
        service = _make_service()
        classroom_id = _seed_classroom(service)

        with pytest.raises(
            ClassroomInputError, match=r"행과 열은 모두 입력하거나 모두 비워야 합니다."
        ):
            service.create_seat(classroom_id, code="S01", label="좌석 1", row=1)


class TestUpdateSeatRowColumn:
    def test_changes_row_column(self) -> None:
        service = _make_service()
        classroom_id = _seed_classroom(service)
        seat = service.create_seat(classroom_id, code="S01", label="좌석 1", row=1, column=1)

        updated = service.update_seat(seat.id, row=2, column=3)

        assert updated.row == 2
        assert updated.column == 3

    def test_omitted_row_column_preserved(self) -> None:
        """row/column을 전달하지 않으면 기존 값을 유지한다."""
        service = _make_service()
        classroom_id = _seed_classroom(service)
        seat = service.create_seat(classroom_id, code="S01", label="좌석 1", row=1, column=1)

        updated = service.update_seat(seat.id, label="이름만 변경")

        assert updated.row == 1
        assert updated.column == 1

    def test_unset_row_column_clears_both(self) -> None:
        service = _make_service()
        classroom_id = _seed_classroom(service)
        seat = service.create_seat(classroom_id, code="S01", label="좌석 1", row=1, column=1)

        updated = service.update_seat(seat.id, unset_row=True, unset_column=True)

        assert updated.row is None
        assert updated.column is None

    def test_partial_unset_raises(self) -> None:
        """한쪽만 해제하면 최종 상태가 부분 입력이 되어 오류가 난다."""
        service = _make_service()
        classroom_id = _seed_classroom(service)
        seat = service.create_seat(classroom_id, code="S01", label="좌석 1", row=1, column=1)

        with pytest.raises(
            ClassroomInputError, match=r"행과 열은 모두 입력하거나 모두 비워야 합니다."
        ):
            service.update_seat(seat.id, unset_row=True)

    def test_unset_column_keeps_existing_row_raises(self) -> None:
        """column만 해제하면 row가 남아 부분 상태가 되어 오류가 난다."""
        service = _make_service()
        classroom_id = _seed_classroom(service)
        seat = service.create_seat(classroom_id, code="S01", label="좌석 1", row=1, column=1)

        with pytest.raises(
            ClassroomInputError, match=r"행과 열은 모두 입력하거나 모두 비워야 합니다."
        ):
            service.update_seat(seat.id, unset_column=True)

    def test_row_below_one_on_update_raises(self) -> None:
        service = _make_service()
        classroom_id = _seed_classroom(service)
        seat = service.create_seat(classroom_id, code="S01", label="좌석 1", row=1, column=1)

        with pytest.raises(ClassroomInputError, match=r"행은 1 이상이어야 합니다."):
            service.update_seat(seat.id, row=0, column=1)


class TestCoordinateUniquenessParity:
    """(row, column) 중복 검증: MongoDB partial unique index와 memory parity.

    - 같은 강의실에서 nonnull (row, column) 조합은 unique하다.
    - update는 자기 자신을 제외하고, 비활성 좌석도 coordinate를 예약한다.
    - row/column이 모두 None인 좌석(null pair)은 중복을 허용한다.
    """

    def test_create_duplicate_coordinate_raises(self) -> None:
        service = _make_service()
        classroom_id = _seed_classroom(service)
        service.create_seat(classroom_id, code="S01", label="좌석 1", row=1, column=1)

        with pytest.raises(SeatDuplicateError):
            service.create_seat(classroom_id, code="S02", label="좌석 2", row=1, column=1)

    def test_update_to_occupied_coordinate_raises(self) -> None:
        service = _make_service()
        classroom_id = _seed_classroom(service)
        service.create_seat(classroom_id, code="S01", label="좌석 1", row=1, column=1)
        other = service.create_seat(classroom_id, code="S02", label="좌석 2", row=2, column=2)

        with pytest.raises(SeatDuplicateError):
            service.update_seat(other.id, row=1, column=1)

    def test_update_to_own_coordinate_is_allowed(self) -> None:
        """자기 자신의 (row, column)으로 수정하면 충돌로 보지 않는다."""
        service = _make_service()
        classroom_id = _seed_classroom(service)
        seat = service.create_seat(classroom_id, code="S01", label="좌석 1", row=1, column=1)

        updated = service.update_seat(seat.id, row=1, column=1, label="이름만 변경")

        assert updated.row == 1
        assert updated.column == 1

    def test_null_pair_duplicate_is_allowed(self) -> None:
        """row/column이 모두 None인 좌석은 같은 강의실에 여러 개 있어도 된다."""
        service = _make_service()
        classroom_id = _seed_classroom(service)

        service.create_seat(classroom_id, code="S01", label="좌석 1")
        service.create_seat(classroom_id, code="S02", label="좌석 2")

    def test_inactive_seat_reserves_coordinate(self) -> None:
        """legacy inactive(PUT is_active=false) 좌석도 (row, column)을 예약한다."""
        service = _make_service()
        classroom_id = _seed_classroom(service)
        service.create_seat(classroom_id, code="S01", label="좌석 1", row=1, column=1)
        inactive = service.create_seat(classroom_id, code="S02", label="좌석 2", row=2, column=2)
        service.update_seat(inactive.id, is_active=False)

        with pytest.raises(SeatDuplicateError):
            service.create_seat(classroom_id, code="S03", label="좌석 3", row=2, column=2)

    def test_hard_deleted_seat_releases_coordinate(self) -> None:
        """hard delete된 좌석의 (row, column)은 즉시 해제된다."""
        service = _make_service()
        classroom_id = _seed_classroom(service)
        service.create_seat(classroom_id, code="S01", label="좌석 1", row=1, column=1)
        deleted = service.create_seat(classroom_id, code="S02", label="좌석 2", row=2, column=2)
        service.delete_seat(deleted.id)

        created = service.create_seat(classroom_id, code="S03", label="좌석 3", row=2, column=2)

        assert (created.row, created.column) == (2, 2)
        assert created.id != deleted.id

    def test_same_coordinate_in_different_classrooms_is_allowed(self) -> None:
        service = _make_service()
        classroom_a = _seed_classroom(service)
        classroom_b = service.seed_classroom(
            CreateClassroomCommand(
                id="classroom-b203",
                code="B203",
                name="일반 강의실 B",
                location="B동 2층",
            )
        ).id

        service.create_seat(classroom_a, code="S01", label="좌석 1", row=1, column=1)
        service.create_seat(classroom_b, code="S01", label="좌석 1", row=1, column=1)


class TestSeedSeatRowColumn:
    def test_seed_seat_keeps_row_column(self) -> None:
        service = _make_service()
        classroom_id = _seed_classroom(service)

        seat = service.seed_seat(
            CreateSeatCommand(
                id="seat-s01",
                classroom_id=classroom_id,
                code="S01",
                label="좌석 1",
                row=2,
                column=4,
            )
        )

        assert seat.row == 2
        assert seat.column == 4

    def test_seed_seat_partial_row_column_raises(self) -> None:
        service = _make_service()
        classroom_id = _seed_classroom(service)

        with pytest.raises(ClassroomInputError):
            service.seed_seat(
                CreateSeatCommand(
                    id="seat-s01",
                    classroom_id=classroom_id,
                    code="S01",
                    label="좌석 1",
                    row=1,
                )
            )


# ============================================================
# 스키마 (SeatCreateRequest / SeatUpdateRequest / SeatResponse)
# ============================================================


class TestSeatSchemas:
    def test_seat_create_request_parses_row_column(self) -> None:
        payload = SeatCreateRequest(code="S01", label="좌석 1", row=3, column=5)
        assert payload.row == 3
        assert payload.column == 5

    def test_seat_create_request_without_row_column(self) -> None:
        payload = SeatCreateRequest(code="S01", label="좌석 1")
        assert payload.row is None
        assert payload.column is None

    def test_seat_update_request_parses_row_column(self) -> None:
        payload = SeatUpdateRequest(row=2, column=4)
        assert payload.row == 2
        assert payload.column == 4
        assert "row" in payload.model_fields_set
        assert "column" in payload.model_fields_set

    def test_seat_update_request_explicit_null_marks_fields_set(self) -> None:
        """명시적 null은 model_fields_set에 남아 해제 신호로 사용된다."""
        payload = SeatUpdateRequest(row=None, column=None)
        assert "row" in payload.model_fields_set
        assert "column" in payload.model_fields_set

    def test_seat_response_from_domain_maps_row_column(self) -> None:
        service = _make_service()
        classroom_id = _seed_classroom(service)
        seat = service.create_seat(classroom_id, code="S01", label="좌석 1", row=1, column=2)

        response = SeatResponse.from_domain(seat)

        assert response.row == 1
        assert response.column == 2

    def test_seat_response_from_domain_without_row_column(self) -> None:
        service = _make_service()
        classroom_id = _seed_classroom(service)
        seat = service.create_seat(classroom_id, code="S01", label="좌석 1")

        response = SeatResponse.from_domain(seat)

        assert response.row is None
        assert response.column is None


# ============================================================
# 라우터 통합 테스트
# ============================================================


@pytest.fixture
def client() -> Iterator[TestClient]:
    service = _make_service()
    app.dependency_overrides[get_classroom_service] = lambda: service
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _create_classroom(client: TestClient) -> str:
    response = client.post(
        "/api/v1/classrooms",
        json={"code": "A101", "name": "일반 강의실", "location": "A동 1층"},
    )
    assert response.status_code == 201
    return str(cast(dict[str, object], response.json())["id"])


def _create_seat(
    client: TestClient,
    classroom_id: str,
    *,
    code: str = "S01",
    label: str = "좌석 1",
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


class TestSeatRouterRowColumn:
    def test_create_seat_with_row_column_returns_them(self, client: TestClient) -> None:
        classroom_id = _create_classroom(client)

        created = _create_seat(client, classroom_id, row=1, column=2)

        assert created["row"] == 1
        assert created["column"] == 2

    def test_create_seat_without_row_column_returns_none(self, client: TestClient) -> None:
        classroom_id = _create_classroom(client)

        created = _create_seat(client, classroom_id)

        assert created["row"] is None
        assert created["column"] is None

    def test_create_seat_with_invalid_row_returns_422(self, client: TestClient) -> None:
        classroom_id = _create_classroom(client)

        response = client.post(
            f"/api/v1/classrooms/{classroom_id}/seats",
            json={"code": "S01", "label": "좌석 1", "row": 0, "column": 1},
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "CLASSROOM_INPUT_INVALID"

    def test_create_seat_with_partial_row_column_returns_422(self, client: TestClient) -> None:
        classroom_id = _create_classroom(client)

        response = client.post(
            f"/api/v1/classrooms/{classroom_id}/seats",
            json={"code": "S01", "label": "좌석 1", "row": 1},
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "CLASSROOM_INPUT_INVALID"

    def test_update_seat_changes_row_column(self, client: TestClient) -> None:
        classroom_id = _create_classroom(client)
        seat_id = str(_create_seat(client, classroom_id)["id"])

        response = client.put(
            f"/api/v1/classrooms/{classroom_id}/seats/{seat_id}",
            json={"row": 2, "column": 3},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["row"] == 2
        assert body["column"] == 3

    def test_update_seat_omitted_row_column_preserved(self, client: TestClient) -> None:
        """PUT에 row/column이 없으면 기존 값을 유지한다."""
        classroom_id = _create_classroom(client)
        seat_id = str(_create_seat(client, classroom_id, row=1, column=1)["id"])

        response = client.put(
            f"/api/v1/classrooms/{classroom_id}/seats/{seat_id}",
            json={"label": "이름만 변경"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["row"] == 1
        assert body["column"] == 1

    def test_update_seat_unset_row_column_via_null(self, client: TestClient) -> None:
        """row/column을 명시적으로 null로 보내면 해제된다."""
        classroom_id = _create_classroom(client)
        seat_id = str(_create_seat(client, classroom_id, row=1, column=1)["id"])

        response = client.put(
            f"/api/v1/classrooms/{classroom_id}/seats/{seat_id}",
            json={"row": None, "column": None},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["row"] is None
        assert body["column"] is None

    def test_update_seat_partial_unset_returns_422(self, client: TestClient) -> None:
        """row만 null로 보내 부분 해제하면 오류가 난다."""
        classroom_id = _create_classroom(client)
        seat_id = str(_create_seat(client, classroom_id, row=1, column=1)["id"])

        response = client.put(
            f"/api/v1/classrooms/{classroom_id}/seats/{seat_id}",
            json={"row": None},
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "CLASSROOM_INPUT_INVALID"

    def test_update_seat_partial_row_column_returns_422(self, client: TestClient) -> None:
        """row만 보내 부분 입력하면 오류가 난다."""
        classroom_id = _create_classroom(client)
        seat_id = str(_create_seat(client, classroom_id)["id"])

        response = client.put(
            f"/api/v1/classrooms/{classroom_id}/seats/{seat_id}",
            json={"row": 1},
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "CLASSROOM_INPUT_INVALID"

    def test_update_seat_invalid_row_returns_422(self, client: TestClient) -> None:
        classroom_id = _create_classroom(client)
        seat_id = str(_create_seat(client, classroom_id)["id"])

        response = client.put(
            f"/api/v1/classrooms/{classroom_id}/seats/{seat_id}",
            json={"row": 0, "column": 1},
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "CLASSROOM_INPUT_INVALID"
