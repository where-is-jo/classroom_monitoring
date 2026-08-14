"""강의실 CRUD API 통합 테스트."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import cast

import pytest
from fastapi.testclient import TestClient

from app.classrooms.adapters.memory_repository import InMemoryClassroomRepository
from app.classrooms.service import ClassroomService
from app.main import app
from app.shared.dependencies import get_classroom_service


@pytest.fixture
def client() -> Iterator[TestClient]:
    service = ClassroomService(
        InMemoryClassroomRepository(),
        occupancy_confidence_threshold=0.5,
        clock=lambda: datetime(2026, 8, 13, 9, 0, tzinfo=UTC),
    )
    app.dependency_overrides[get_classroom_service] = lambda: service
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _create_classroom(
    client: TestClient,
    *,
    code: str = "A101",
    name: str = "일반 강의실",
    location: str = "A동 1층",
) -> dict[str, object]:
    response = client.post(
        "/api/v1/classrooms",
        json={"code": code, "name": name, "location": location},
    )
    assert response.status_code == 201
    return cast(dict[str, object], response.json())


def _create_seat(
    client: TestClient,
    classroom_id: str,
    *,
    code: str = "S01",
    label: str = "좌석 1",
    geometry: dict[str, float] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {"code": code, "label": label}
    if geometry is not None:
        payload["geometry"] = geometry
    response = client.post(
        f"/api/v1/classrooms/{classroom_id}/seats",
        json=payload,
    )
    assert response.status_code == 201
    return cast(dict[str, object], response.json())


def test_create_returns_location_header(client: TestClient) -> None:
    """생성 응답은 Location 헤더로 새 리소스 경로를 알려준다."""
    created = client.post(
        "/api/v1/classrooms",
        json={"code": "B101", "name": "실습실", "location": "B동 1층"},
    )
    assert created.status_code == 201
    classroom_id = created.json()["id"]
    assert created.headers["location"] == f"/api/v1/classrooms/{classroom_id}"


def test_create_get_update_delete_flow(client: TestClient) -> None:
    """생성·상세 조회·수정·삭제가 한 흐름으로 동작한다."""
    created = _create_classroom(client)
    classroom_id = str(created["id"])
    assert created["code"] == "A101"
    assert created["is_active"] is True
    assert "created_at" in created

    fetched = client.get(f"/api/v1/classrooms/{classroom_id}")
    assert fetched.status_code == 200
    assert fetched.json()["code"] == "A101"
    assert fetched.json()["is_active"] is True

    updated = client.put(
        f"/api/v1/classrooms/{classroom_id}",
        json={"code": "A102", "name": "수정된 강의실", "location": "A동 2층"},
    )
    assert updated.status_code == 200
    assert updated.json()["code"] == "A102"
    assert updated.json()["name"] == "수정된 강의실"
    assert updated.json()["location"] == "A동 2층"

    deleted = client.delete(f"/api/v1/classrooms/{classroom_id}")
    assert deleted.status_code == 204

    gone = client.get(f"/api/v1/classrooms/{classroom_id}")
    assert gone.status_code == 404
    listing = client.get("/api/v1/classrooms")
    assert listing.status_code == 200
    assert listing.json()["total"] == 0


def test_update_partial_fields_preserves_others(client: TestClient) -> None:
    """PUT에서 전달하지 않은 필드는 기존 값을 유지한다."""
    created = _create_classroom(client)
    classroom_id = str(created["id"])

    updated = client.put(f"/api/v1/classrooms/{classroom_id}", json={"name": "이름만 변경"})
    assert updated.status_code == 200
    body = updated.json()
    assert body["name"] == "이름만 변경"
    assert body["code"] == "A101"
    assert body["location"] == "A동 1층"
    assert body["is_active"] is True

    deactivated = client.put(f"/api/v1/classrooms/{classroom_id}", json={"is_active": False})
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False
    assert client.get(f"/api/v1/classrooms/{classroom_id}").status_code == 404


def test_create_with_duplicate_code_returns_conflict(client: TestClient) -> None:
    """같은 코드(대소문자 무시)를 만들면 409를 돌려준다."""
    _create_classroom(client, code="A101")
    response = client.post(
        "/api/v1/classrooms",
        json={"code": "a101", "name": "중복 강의실", "location": "B동"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CLASSROOM_DUPLICATE"


def test_update_to_duplicate_code_returns_conflict(client: TestClient) -> None:
    """다른 강의실이 쓰는 코드로 수정하면 409를 돌려준다."""
    _create_classroom(client, code="A101")
    other = _create_classroom(client, code="B203")
    other_id = str(other["id"])

    response = client.put(f"/api/v1/classrooms/{other_id}", json={"code": "A101"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CLASSROOM_DUPLICATE"


def test_unknown_classroom_returns_not_found(client: TestClient) -> None:
    """존재하지 않는 강의실 조회·수정·삭제는 404를 돌려준다."""
    assert client.get("/api/v1/classrooms/missing").status_code == 404
    assert client.put("/api/v1/classrooms/missing", json={"name": "이름"}).status_code == 404
    assert client.delete("/api/v1/classrooms/missing").status_code == 404


def test_invalid_input_returns_422(client: TestClient) -> None:
    """형식이 잘못된 입력은 422를 돌려준다."""
    empty_code = client.post(
        "/api/v1/classrooms",
        json={"code": "   ", "name": "이름", "location": "위치"},
    )
    assert empty_code.status_code == 422
    assert empty_code.json()["error"]["code"] == "CLASSROOM_INPUT_INVALID"

    missing_field = client.post("/api/v1/classrooms", json={"code": "A101"})
    assert missing_field.status_code == 422

    created = _create_classroom(client)
    classroom_id = str(created["id"])
    empty_name = client.put(f"/api/v1/classrooms/{classroom_id}", json={"name": "   "})
    assert empty_name.status_code == 422
    assert empty_name.json()["error"]["code"] == "CLASSROOM_INPUT_INVALID"


def test_seat_create_returns_location_header(client: TestClient) -> None:
    """좌석 생성 응답은 Location 헤더로 새 리소스 경로를 알려준다."""
    classroom = _create_classroom(client)
    classroom_id = str(classroom["id"])
    created = client.post(
        f"/api/v1/classrooms/{classroom_id}/seats",
        json={"code": "S01", "label": "좌석 1"},
    )
    assert created.status_code == 201
    seat_id = created.json()["id"]
    assert created.headers["location"] == (f"/api/v1/classrooms/{classroom_id}/seats/{seat_id}")


def test_seat_create_get_update_delete_flow(client: TestClient) -> None:
    """좌석 생성·상세 조회·수정·삭제가 한 흐름으로 동작한다."""
    classroom = _create_classroom(client)
    classroom_id = str(classroom["id"])
    created = _create_seat(client, classroom_id)
    seat_id = str(created["id"])
    assert created["code"] == "S01"
    assert created["classroom_id"] == classroom_id
    assert created["is_active"] is True
    occupancy = cast(dict[str, object], created["current_occupancy"])
    assert occupancy["state"] == "UNKNOWN"
    assert "created_at" in created
    assert "updated_at" in created

    fetched = client.get(f"/api/v1/classrooms/{classroom_id}/seats/{seat_id}")
    assert fetched.status_code == 200
    assert fetched.json()["code"] == "S01"
    assert fetched.json()["is_active"] is True

    updated = client.put(
        f"/api/v1/classrooms/{classroom_id}/seats/{seat_id}",
        json={"code": "S02", "label": "수정된 좌석"},
    )
    assert updated.status_code == 200
    assert updated.json()["code"] == "S02"
    assert updated.json()["label"] == "수정된 좌석"

    deleted = client.delete(f"/api/v1/classrooms/{classroom_id}/seats/{seat_id}")
    assert deleted.status_code == 204

    gone = client.get(f"/api/v1/classrooms/{classroom_id}/seats/{seat_id}")
    assert gone.status_code == 404
    listing = client.get(f"/api/v1/classrooms/{classroom_id}/seats")
    assert listing.status_code == 200
    assert listing.json()["total"] == 0


def test_seat_update_partial_fields_preserves_others(client: TestClient) -> None:
    """PUT에서 전달하지 않은 좌석 필드는 기존 값을 유지한다."""
    classroom = _create_classroom(client)
    classroom_id = str(classroom["id"])
    created = _create_seat(
        client,
        classroom_id,
        geometry={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
    )
    seat_id = str(created["id"])

    updated = client.put(
        f"/api/v1/classrooms/{classroom_id}/seats/{seat_id}",
        json={"label": "이름만 변경"},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["label"] == "이름만 변경"
    assert body["code"] == "S01"
    assert body["geometry"] == {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}
    assert body["is_active"] is True

    deactivated = client.put(
        f"/api/v1/classrooms/{classroom_id}/seats/{seat_id}",
        json={"is_active": False},
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False
    assert client.get(f"/api/v1/classrooms/{classroom_id}/seats/{seat_id}").status_code == 404


def test_seat_duplicate_code_returns_conflict(client: TestClient) -> None:
    """같은 강의실에 중복 좌석 코드(대소문자 무시)를 만들면 409를 돌려준다."""
    classroom = _create_classroom(client)
    classroom_id = str(classroom["id"])
    _create_seat(client, classroom_id, code="S01")
    response = client.post(
        f"/api/v1/classrooms/{classroom_id}/seats",
        json={"code": "s01", "label": "중복 좌석"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SEAT_DUPLICATE"


def test_update_seat_to_duplicate_code_returns_conflict(client: TestClient) -> None:
    """다른 좌석이 쓰는 코드로 수정하면 409를 돌려준다."""
    classroom = _create_classroom(client)
    classroom_id = str(classroom["id"])
    _create_seat(client, classroom_id, code="S01")
    other = _create_seat(client, classroom_id, code="S02")
    other_id = str(other["id"])

    response = client.put(
        f"/api/v1/classrooms/{classroom_id}/seats/{other_id}",
        json={"code": "S01"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SEAT_DUPLICATE"


def test_same_seat_code_allowed_in_different_classrooms(client: TestClient) -> None:
    """좌석 코드 중복은 같은 강의실 안에서만 판정한다."""
    first = _create_classroom(client, code="A101")
    second = _create_classroom(client, code="B203")
    first_seat = _create_seat(client, str(first["id"]), code="S01")
    second_seat = _create_seat(client, str(second["id"]), code="S01")
    assert first_seat["id"] != second_seat["id"]


def test_seat_list_returns_all_seats_sorted(client: TestClient) -> None:
    """좌석 목록은 코드순으로 정렬해 반환한다."""
    classroom = _create_classroom(client)
    classroom_id = str(classroom["id"])
    _create_seat(client, classroom_id, code="S02", label="좌석 2")
    _create_seat(client, classroom_id, code="S01", label="좌석 1")

    listing = client.get(f"/api/v1/classrooms/{classroom_id}/seats")
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 2
    assert [item["code"] for item in body["items"]] == ["S01", "S02"]


def test_seat_in_unknown_classroom_returns_not_found(client: TestClient) -> None:
    """존재하지 않는 강의실에 좌석을 만들거나 조회하면 404를 돌려준다."""
    assert (
        client.post(
            "/api/v1/classrooms/missing/seats",
            json={"code": "S01", "label": "좌석"},
        ).status_code
        == 404
    )
    assert client.get("/api/v1/classrooms/missing/seats").status_code == 404


def test_unknown_seat_returns_not_found(client: TestClient) -> None:
    """존재하지 않는 좌석 조회·수정·삭제는 404를 돌려준다."""
    classroom = _create_classroom(client)
    classroom_id = str(classroom["id"])
    assert client.get(f"/api/v1/classrooms/{classroom_id}/seats/missing").status_code == 404
    assert (
        client.put(
            f"/api/v1/classrooms/{classroom_id}/seats/missing", json={"label": "이름"}
        ).status_code
        == 404
    )
    assert client.delete(f"/api/v1/classrooms/{classroom_id}/seats/missing").status_code == 404


def test_seat_operations_via_other_classroom_returns_404(client: TestClient) -> None:
    """URL classroom_id와 좌석 소속이 다르면 조회·수정·삭제가 404 SEAT_NOT_FOUND다."""
    classroom_a = _create_classroom(client, code="A101")
    classroom_b = _create_classroom(client, code="B203")
    seat = _create_seat(client, str(classroom_a["id"]))
    seat_id = str(seat["id"])
    other_classroom_id = str(classroom_b["id"])

    response = client.get(f"/api/v1/classrooms/{other_classroom_id}/seats/{seat_id}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SEAT_NOT_FOUND"

    response = client.put(
        f"/api/v1/classrooms/{other_classroom_id}/seats/{seat_id}",
        json={"label": "변경 시도"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SEAT_NOT_FOUND"

    response = client.delete(f"/api/v1/classrooms/{other_classroom_id}/seats/{seat_id}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SEAT_NOT_FOUND"

    # 소속 불일치 요청은 원래 좌석에 영향을 주지 않는다.
    assert client.get(f"/api/v1/classrooms/{classroom_a['id']}/seats/{seat_id}").status_code == 200
    assert (
        client.delete(f"/api/v1/classrooms/{classroom_a['id']}/seats/{seat_id}").status_code == 204
    )


def test_seat_geometry_roundtrip(client: TestClient) -> None:
    """geometry 좌표가 생성·조회·수정에서 그대로 유지된다."""
    classroom = _create_classroom(client)
    classroom_id = str(classroom["id"])
    geometry = {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}
    created = _create_seat(client, classroom_id, geometry=geometry)
    seat_id = str(created["id"])
    assert created["geometry"] == geometry

    fetched = client.get(f"/api/v1/classrooms/{classroom_id}/seats/{seat_id}")
    assert fetched.status_code == 200
    assert fetched.json()["geometry"] == geometry

    new_geometry = {"x": 0.5, "y": 0.5, "width": 0.2, "height": 0.2}
    updated = client.put(
        f"/api/v1/classrooms/{classroom_id}/seats/{seat_id}",
        json={"geometry": new_geometry},
    )
    assert updated.status_code == 200
    assert updated.json()["geometry"] == new_geometry

    without_geometry = _create_seat(client, classroom_id, code="S02", label="좌석 2")
    assert without_geometry["geometry"] is None


def test_invalid_seat_input_returns_422(client: TestClient) -> None:
    """형식이 잘못된 좌석 입력은 422를 돌려준다."""
    classroom = _create_classroom(client)
    classroom_id = str(classroom["id"])

    empty_code = client.post(
        f"/api/v1/classrooms/{classroom_id}/seats",
        json={"code": "   ", "label": "좌석"},
    )
    assert empty_code.status_code == 422
    assert empty_code.json()["error"]["code"] == "CLASSROOM_INPUT_INVALID"

    out_of_bounds = client.post(
        f"/api/v1/classrooms/{classroom_id}/seats",
        json={
            "code": "S01",
            "label": "좌석",
            "geometry": {"x": 0.9, "y": 0.1, "width": 0.2, "height": 0.2},
        },
    )
    assert out_of_bounds.status_code == 422
    assert out_of_bounds.json()["error"]["code"] == "CLASSROOM_INPUT_INVALID"
