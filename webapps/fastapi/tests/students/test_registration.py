"""학생 등록 서비스와 API 테스트."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.shared.dependencies import get_student_service
from app.students.adapters.memory import InMemoryStudentRepository
from app.students.errors import StudentDuplicateError, StudentInputError
from app.students.models import CreateStudentCommand, RegisterStudentFaceCommand
from app.students.service import StudentService

NOW = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)


def make_service() -> StudentService:
    return StudentService(InMemoryStudentRepository(), clock=lambda: NOW)


def command(**changes: object) -> CreateStudentCommand:
    values: dict[str, object] = {
        "student_number": "ST-2026-001",
        "name": "김민지",
        "birth_date": date(2012, 5, 3),
        "classroom_name": "중등 수학 A반",
        "phone": "010-1234-5678",
        "guardian_phone": "010-9876-5432",
        "face_enrollment_id": "enrollment-001",
    }
    values.update(changes)
    return CreateStudentCommand(
        student_number=cast(str, values["student_number"]),
        name=cast(str, values["name"]),
        birth_date=cast(date, values["birth_date"]),
        classroom_name=cast(str, values["classroom_name"]),
        phone=cast(str | None, values["phone"]),
        guardian_phone=cast(str, values["guardian_phone"]),
        face_enrollment_id=cast(str | None, values["face_enrollment_id"]),
    )


def test_create_student_saves_all_fields_and_face_reference() -> None:
    repository = InMemoryStudentRepository()
    service = StudentService(repository, clock=lambda: NOW)

    created = service.create(command())

    saved = repository.get_student(created.id)
    assert saved == created
    assert saved is not None
    assert UUID(saved.id).version == 4
    assert saved.id != saved.student_number
    assert saved.birth_date == date(2012, 5, 3)
    assert saved.classroom_name == "중등 수학 A반"
    assert saved.phone == "010-1234-5678"
    assert saved.guardian_phone == "010-9876-5432"
    assert saved.face_enrollment_id == "enrollment-001"
    assert saved.face_registered is True


def test_student_number_is_unique() -> None:
    service = make_service()
    service.create(command())

    with pytest.raises(StudentDuplicateError):
        service.create(command(name="다른 학생"))


def test_face_enrollment_is_linked_after_student_creation() -> None:
    repository = InMemoryStudentRepository()
    service = StudentService(repository, clock=lambda: NOW)
    student = service.create(command(face_enrollment_id=None))

    updated = service.register_face(
        RegisterStudentFaceCommand(student_id=student.id, enrollment_id="enrollment-new")
    )

    assert updated.face_registered is True
    assert updated.face_enrollment_id == "enrollment-new"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"student_number": "한글 번호"}, "학생 번호"),
        ({"birth_date": NOW.date() + timedelta(days=1)}, "생년월일"),
        ({"guardian_phone": "123"}, "보호자 연락처"),
        ({"phone": "not-a-phone"}, "학생 연락처"),
    ],
)
def test_invalid_student_input_is_rejected(changes: dict[str, object], message: str) -> None:
    with pytest.raises(StudentInputError, match=message):
        make_service().create(command(**changes))


@pytest.fixture
def client() -> Iterator[TestClient]:
    service = make_service()
    app.dependency_overrides[get_student_service] = lambda: service
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def test_registration_page_posts_student_to_api(client: TestClient) -> None:
    page = client.get("/students/new")
    assert page.status_code == 200
    assert "/api/v1/students" in client.get("/static/student-registration.js").text
    assert "서버나 데이터베이스로 전송되지 않습니다" not in page.text

    response = client.post(
        "/api/v1/students",
        json={
            "student_number": "ST-2026-001",
            "name": "김민지",
            "birth_date": "2012-05-03",
            "classroom_name": "중등 수학 A반",
            "phone": "010-1234-5678",
            "guardian_phone": "010-9876-5432",
            "face_enrollment_id": "enrollment-001",
        },
    )

    assert response.status_code == 201
    assert response.headers["location"] == f"/api/v1/students/{response.json()['id']}"
    body = response.json()
    assert UUID(body["id"]).version == 4
    assert body["id"] != body["student_number"]
    assert body["face_registered"] is True
    assert "phone" not in body
    assert "guardian_phone" not in body


def test_duplicate_student_api_returns_conflict(client: TestClient) -> None:
    payload = {
        "student_number": "ST-2026-001",
        "name": "김민지",
        "birth_date": "2012-05-03",
        "classroom_name": "중등 수학 A반",
        "phone": None,
        "guardian_phone": "010-9876-5432",
        "face_enrollment_id": None,
    }
    assert client.post("/api/v1/students", json=payload).status_code == 201
    duplicate = client.post("/api/v1/students", json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "STUDENT_DUPLICATE"
