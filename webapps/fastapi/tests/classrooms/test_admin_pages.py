"""강의실·좌석 관리 화면 렌더링과 입력 검증 테스트.

관리 화면 폼은 JSON API(`/api/v1/...`)에 fetch로 제출된다. 그래서 화면 테스트는
① 페이지가 올바른 폼과 API 배선(`data-api-url`·`data-api-method`)을 렌더링하는지,
② 대상이 없으면 목록으로 리다이렉트하는지, ③ 화면이 표시할 API 검증 오류가
message를 포함하는지 확인한다. 실제 검증 로직은 `test_crud_api.py`가 담당한다.
"""

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


# --- 강의실 화면 --------------------------------------------------------------


def test_create_page_renders_form(client: TestClient) -> None:
    """강의실 생성 화면은 POST /api/v1/classrooms로 제출하는 폼을 렌더링한다."""
    response = client.get("/classrooms/create")
    assert response.status_code == 200
    assert "강의실 등록" in response.text
    assert 'data-api-url="/api/v1/classrooms"' in response.text
    assert 'data-api-method="POST"' in response.text
    assert 'name="code"' in response.text
    assert 'name="name"' in response.text
    assert 'name="location"' in response.text


def test_edit_page_renders_prefilled_values(client: TestClient) -> None:
    """강의실 수정 화면은 기존 값과 PUT API 배선을 렌더링한다."""
    classroom = _create_classroom(client, code="B101", name="실습실", location="B동 1층")
    classroom_id = str(classroom["id"])

    response = client.get(f"/classrooms/{classroom_id}/edit")

    assert response.status_code == 200
    assert "강의실 수정" in response.text
    assert f'data-api-url="/api/v1/classrooms/{classroom_id}"' in response.text
    assert 'data-api-method="PUT"' in response.text
    assert 'value="B101"' in response.text
    assert 'value="실습실"' in response.text
    assert 'value="B동 1층"' in response.text
    assert 'name="is_active"' in response.text


def test_edit_page_redirects_when_classroom_missing(client: TestClient) -> None:
    """없는 강의실 수정 화면은 목록으로 리다이렉트한다."""
    response = client.get("/classrooms/missing/edit", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/classrooms"


# --- 좌석 목록 화면 ------------------------------------------------------------


def test_seats_page_renders_list_and_map(client: TestClient) -> None:
    """좌석 목록 화면은 배치도·목록·수정/삭제 배선을 렌더링한다."""
    classroom = _create_classroom(client)
    classroom_id = str(classroom["id"])
    seat = _create_seat(
        client,
        classroom_id,
        code="S01",
        label="좌석 1",
        geometry={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
    )
    _create_seat(client, classroom_id, code="S02", label="좌석 2")
    seat_id = str(seat["id"])

    response = client.get(f"/classrooms/{classroom_id}/seats")

    assert response.status_code == 200
    assert "좌석 관리" in response.text
    assert "좌석 배치" in response.text
    assert "좌석 목록" in response.text
    assert "좌석 1" in response.text
    assert "좌석 2" in response.text
    assert f'href="/classrooms/{classroom_id}/seats/{seat_id}/edit"' in response.text
    assert f'data-api-url="/api/v1/classrooms/{classroom_id}/seats/{seat_id}"' in response.text
    assert 'data-api-method="DELETE"' in response.text
    assert f'href="/classrooms/{classroom_id}/seats/create"' in response.text


def test_seats_page_renders_empty_state(client: TestClient) -> None:
    """좌석이 없으면 배치도 없이 빈 상태 안내를 렌더링한다."""
    classroom = _create_classroom(client)
    classroom_id = str(classroom["id"])

    response = client.get(f"/classrooms/{classroom_id}/seats")

    assert response.status_code == 200
    assert "등록된 좌석이 없습니다." in response.text
    assert "좌석 배치" not in response.text


def test_seats_page_redirects_when_classroom_missing(client: TestClient) -> None:
    """없는 강의실 좌석 목록 화면은 목록으로 리다이렉트한다."""
    response = client.get("/classrooms/missing/seats", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/classrooms"


# --- 좌석 생성/수정 화면 -------------------------------------------------------


def test_seat_create_page_renders_form(client: TestClient) -> None:
    """좌석 추가 화면은 POST API와 geometry 입력 필드를 렌더링한다."""
    classroom = _create_classroom(client)
    classroom_id = str(classroom["id"])

    response = client.get(f"/classrooms/{classroom_id}/seats/create")

    assert response.status_code == 200
    assert "좌석 추가" in response.text
    assert f'data-api-url="/api/v1/classrooms/{classroom_id}/seats"' in response.text
    assert 'data-api-method="POST"' in response.text
    assert 'name="code"' in response.text
    assert 'name="label"' in response.text
    assert 'name="geometry_x"' in response.text
    assert 'name="geometry_y"' in response.text
    assert 'name="geometry_width"' in response.text
    assert 'name="geometry_height"' in response.text
    assert 'name="is_active"' not in response.text


def test_seat_edit_page_renders_prefilled_values(client: TestClient) -> None:
    """좌석 수정 화면은 기존 값과 PUT API 배선을 렌더링한다."""
    classroom = _create_classroom(client)
    classroom_id = str(classroom["id"])
    seat = _create_seat(
        client,
        classroom_id,
        geometry={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
    )
    seat_id = str(seat["id"])

    response = client.get(f"/classrooms/{classroom_id}/seats/{seat_id}/edit")

    assert response.status_code == 200
    assert "좌석 1 수정" in response.text
    assert f'data-api-url="/api/v1/classrooms/{classroom_id}/seats/{seat_id}"' in response.text
    assert 'data-api-method="PUT"' in response.text
    assert 'value="S01"' in response.text
    assert 'value="좌석 1"' in response.text
    assert 'value="0.1"' in response.text
    assert 'value="0.4"' in response.text
    assert 'name="is_active"' in response.text


def test_seat_edit_page_redirects_when_seat_missing(client: TestClient) -> None:
    """없는 좌석 수정 화면은 좌석 목록으로 리다이렉트한다."""
    classroom = _create_classroom(client)
    classroom_id = str(classroom["id"])

    response = client.get(
        f"/classrooms/{classroom_id}/seats/missing/edit",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == f"/classrooms/{classroom_id}/seats"


def test_seat_edit_page_redirects_when_classroom_missing(client: TestClient) -> None:
    """없는 강의실의 좌석 수정 화면은 좌석 목록으로 리다이렉트한다."""
    response = client.get("/classrooms/missing/seats/missing/edit", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/classrooms/missing/seats"


def test_seat_create_page_redirects_when_classroom_missing(client: TestClient) -> None:
    """없는 강의실의 좌석 추가 화면은 목록으로 리다이렉트한다."""
    response = client.get("/classrooms/missing/seats/create", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/classrooms"


# --- 화면이 표시할 입력 검증 오류 ----------------------------------------------


def test_classroom_validation_error_has_displayable_message(client: TestClient) -> None:
    """강의실 생성 폼이 제출할 API의 검증 오류는 화면 표시용 message를 담는다."""
    invalid = client.post(
        "/api/v1/classrooms",
        json={"code": "   ", "name": "이름", "location": "위치"},
    )
    assert invalid.status_code == 422
    message = invalid.json()["error"]["message"]
    assert isinstance(message, str) and message

    duplicate = client.post(
        "/api/v1/classrooms",
        json={"code": "D101", "name": "중복", "location": "D동"},
    )
    assert duplicate.status_code == 201
    duplicate_code = client.post(
        "/api/v1/classrooms",
        json={"code": "d101", "name": "중복 강의실", "location": "D동"},
    )
    assert duplicate_code.status_code == 409
    message = duplicate_code.json()["error"]["message"]
    assert isinstance(message, str) and message


def test_seat_validation_error_has_displayable_message(client: TestClient) -> None:
    """좌석 생성 폼이 제출할 API의 검증 오류는 화면 표시용 message를 담는다."""
    classroom = _create_classroom(client)
    classroom_id = str(classroom["id"])

    invalid = client.post(
        f"/api/v1/classrooms/{classroom_id}/seats",
        json={
            "code": "S01",
            "label": "좌석",
            "geometry": {"x": 0.9, "y": 0.1, "width": 0.2, "height": 0.2},
        },
    )
    assert invalid.status_code == 422
    message = invalid.json()["error"]["message"]
    assert isinstance(message, str) and message

    duplicate = client.post(
        f"/api/v1/classrooms/{classroom_id}/seats",
        json={"code": "S01", "label": "좌석 1"},
    )
    assert duplicate.status_code == 201
    duplicate_code = client.post(
        f"/api/v1/classrooms/{classroom_id}/seats",
        json={"code": "s01", "label": "중복 좌석"},
    )
    assert duplicate_code.status_code == 409
    message = duplicate_code.json()["error"]["message"]
    assert isinstance(message, str) and message
