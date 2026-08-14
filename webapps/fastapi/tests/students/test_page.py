from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_student_registration_page_has_minimal_form_and_notice() -> None:
    with TestClient(app) as client:
        response = client.get("/students/new")

    assert response.status_code == 200
    assert "학생 등록" in response.text
    assert 'id="student-registration-form"' in response.text
    assert 'id="student-save-notice"' in response.text
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


def test_student_registration_page_embeds_face_enrollment_modal() -> None:
    with TestClient(app) as client:
        response = client.get("/students/new")

    assert response.status_code == 200
    assert 'id="open-face-enrollment"' in response.text
    assert '<dialog id="student-face-enrollment-dialog"' in response.text
    assert 'id="consent-confirmed"' in response.text
    assert 'id="start-enrollment"' in response.text
    assert 'id="camera-preview"' in response.text
    assert 'id="overall-progress"' in response.text
    assert 'id="face-enrollment-complete"' in response.text
    assert 'id="pose-progress"' not in response.text


def test_navigation_contains_student_registration_link() -> None:
    with TestClient(app) as client:
        response = client.get("/students/new")

    assert 'href="/students/new"' in response.text
    assert 'href="/students/new" aria-current="page"' in response.text
