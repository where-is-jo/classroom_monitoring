from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_student_management_page_has_list_and_registration_modal() -> None:
    with TestClient(app) as client:
        response = client.get("/students")

    assert response.status_code == 200
    assert "학생 관리" in response.text
    assert 'id="student-list-title"' in response.text
    assert 'id="open-student-registration"' in response.text
    assert '<dialog id="student-registration-dialog"' in response.text
    assert 'id="student-registration-form"' in response.text
    for field_name in (
        "name",
        "student_number",
        "birth_date",
        "classroom_name",
        "phone",
        "guardian_phone",
    ):
        assert f'name="{field_name}"' in response.text
    assert "alert(" not in response.text


def test_student_management_page_embeds_face_enrollment_modal() -> None:
    with TestClient(app) as client:
        response = client.get("/students/new")

    assert response.status_code == 200
    assert 'class="face-status face-status-needed open-face-enrollment"' in response.text
    assert '<dialog id="student-face-enrollment-dialog"' in response.text
    assert 'id="consent-confirmed"' in response.text
    assert 'id="start-enrollment"' in response.text
    assert 'id="camera-preview"' in response.text
    assert 'id="overall-progress"' in response.text
    assert 'id="face-enrollment-complete"' in response.text
    assert 'id="pose-progress"' not in response.text


def test_navigation_contains_student_management_link() -> None:
    with TestClient(app) as client:
        response = client.get("/students/new")

    assert 'href="/students"' in response.text
    assert 'href="/students" aria-current="page"' in response.text
