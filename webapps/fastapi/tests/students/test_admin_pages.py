"""학생 관리 화면 렌더링과 배선 테스트.

관리 화면 폼은 JSON API(`/api/v1/...`)에 fetch로 제출된다. 그래서 화면 테스트는
① 페이지가 올바른 폼과 API 배선(`data-api-url`·`data-api-method`)을 렌더링하는지,
② 학생이 없으면 빈 상태를 보여주는지, ③ 대상이 없으면 목록으로 리다이렉트하는지,
④ 화면이 표시할 API 검증 오류가 message를 포함하는지 확인한다.
실제 검증 로직은 `test_router.py`가 담당한다.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import cast

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.shared.dependencies import get_student_service
from app.students.adapters.memory_repository import InMemoryStudentRepository
from app.students.service import StudentService


@pytest.fixture
def client() -> Iterator[TestClient]:
    service = StudentService(
        InMemoryStudentRepository(),
        clock=lambda: datetime(2026, 8, 13, 9, 0, tzinfo=UTC),
    )
    app.dependency_overrides[get_student_service] = lambda: service
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _create_student(
    client: TestClient,
    *,
    student_no: str = "20240001",
    name: str = "김철수",
    department: str = "컴퓨터공학과",
) -> dict[str, object]:
    response = client.post(
        "/api/v1/students",
        json={"student_no": student_no, "name": name, "department": department},
    )
    assert response.status_code == 201
    return cast(dict[str, object], response.json())


# --- 학생 목록 화면 ------------------------------------------------------------


def test_list_page_renders_students(client: TestClient) -> None:
    """학생 목록 화면은 등록된 학생과 수정·비활성화 배선을 렌더링한다."""
    student = _create_student(client, student_no="20240001", name="김철수")
    student_id = str(student["id"])

    response = client.get("/students")

    assert response.status_code == 200
    assert "학생 관리" in response.text
    assert "20240001" in response.text
    assert "김철수" in response.text
    assert "컴퓨터공학과" in response.text
    assert "활성" in response.text
    assert f'href="/students/{student_id}/edit"' in response.text
    assert f'data-api-url="/api/v1/students/{student_id}"' in response.text
    assert 'data-api-method="DELETE"' in response.text
    assert 'data-confirm="김철수 학생을 비활성화할까요?"' in response.text
    assert 'href="/students/create"' in response.text


def test_list_page_marks_inactive_student(client: TestClient) -> None:
    """비활성 학생은 비활성 문구를 보여주고 비활성화 버튼을 노출하지 않는다."""
    student = _create_student(client, student_no="20240002", name="이영희")
    student_id = str(student["id"])
    deactivate = client.delete(f"/api/v1/students/{student_id}")
    assert deactivate.status_code == 204

    response = client.get("/students")

    assert response.status_code == 200
    assert "비활성" in response.text
    assert 'data-confirm="이영희 학생을 비활성화할까요?"' not in response.text


def test_list_page_renders_empty_state(client: TestClient) -> None:
    """학생이 없으면 목록 대신 빈 상태 안내를 렌더링한다."""
    response = client.get("/students")

    assert response.status_code == 200
    assert "등록된 학생이 없습니다." in response.text
    assert 'id="student-list-title"' not in response.text


# --- 학생 생성/수정 화면 -------------------------------------------------------


def test_create_page_renders_form(client: TestClient) -> None:
    """학생 등록 화면은 POST /api/v1/students로 제출하는 폼을 렌더링한다."""
    response = client.get("/students/create")

    assert response.status_code == 200
    assert "학생 등록" in response.text
    assert 'data-api-url="/api/v1/students"' in response.text
    assert 'data-api-method="POST"' in response.text
    assert 'name="student_no"' in response.text
    assert 'name="name"' in response.text
    assert 'name="department"' in response.text


def test_edit_page_renders_prefilled_values(client: TestClient) -> None:
    """학생 수정 화면은 기존 값과 PATCH API 배선을 렌더링한다."""
    student = _create_student(client, student_no="20240003", name="박지민", department="디자인학과")
    student_id = str(student["id"])

    response = client.get(f"/students/{student_id}/edit")

    assert response.status_code == 200
    assert "박지민 수정" in response.text
    assert f'data-api-url="/api/v1/students/{student_id}"' in response.text
    assert 'data-api-method="PATCH"' in response.text
    assert 'value="박지민"' in response.text
    assert 'value="디자인학과"' in response.text
    assert 'name="is_active"' in response.text


def test_edit_page_redirects_when_student_missing(client: TestClient) -> None:
    """없는 학생 수정 화면은 목록으로 리다이렉트한다."""
    response = client.get("/students/missing/edit", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/students"


# --- 화면이 표시할 입력 검증 오류 ----------------------------------------------


def test_student_validation_error_has_displayable_message(client: TestClient) -> None:
    """학생 생성 폼이 제출할 API의 검증 오류는 화면 표시용 message를 담는다."""
    invalid = client.post(
        "/api/v1/students",
        json={"student_no": "   ", "name": "이름", "department": "소속"},
    )
    assert invalid.status_code == 422
    message = invalid.json()["error"]["message"]
    assert isinstance(message, str) and message

    duplicate = client.post(
        "/api/v1/students",
        json={"student_no": "20240004", "name": "정수정", "department": "영문학과"},
    )
    assert duplicate.status_code == 201
    duplicate_code = client.post(
        "/api/v1/students",
        json={"student_no": "20240004", "name": "다른사람", "department": "수학과"},
    )
    assert duplicate_code.status_code == 409
    message = duplicate_code.json()["error"]["message"]
    assert isinstance(message, str) and message
