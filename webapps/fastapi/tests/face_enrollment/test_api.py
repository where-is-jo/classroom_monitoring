from __future__ import annotations

from fastapi.testclient import TestClient

from app.face_enrollment.router import delete_face_profile
from app.main import app


def test_create_get_and_abort_enrollment() -> None:
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/students/student-api/face-enrollments",
            json={"consent_confirmed": True, "consent_confirmed_by": "admin"},
        )
        assert created.status_code == 201
        enrollment_id = created.json()["id"]
        assert created.headers["location"].endswith(enrollment_id)
        fetched = client.get(f"/api/v1/face-enrollments/{enrollment_id}")
        assert fetched.status_code == 200
        assert fetched.json()["student_id"] == "student-api"
        deleted = client.delete(f"/api/v1/face-enrollments/{enrollment_id}")
        assert deleted.status_code == 204


def test_consent_is_required() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/students/student-no-consent/face-enrollments",
            json={"consent_confirmed": False, "consent_confirmed_by": "admin"},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "FACE_CONSENT_REQUIRED"


def test_delete_face_profile_removes_gallery_embedding_first() -> None:
    calls: list[str] = []

    class EnrollmentService:
        def delete_profile(self, student_id: str) -> None:
            calls.append(f"profile:{student_id}")

    class EmbeddingService:
        def delete_for_student(self, student_id: str) -> None:
            calls.append(f"embedding:{student_id}")

    response = delete_face_profile(
        "student-delete",
        service=EnrollmentService(),  # type: ignore[arg-type]
        embedding_service=EmbeddingService(),  # type: ignore[arg-type]
    )

    assert response.status_code == 204
    assert calls == ["embedding:student-delete", "profile:student-delete"]


def test_face_enrollment_page_has_camera_controls() -> None:
    with TestClient(app) as client:
        response = client.get("/students/student-ui/face-enrollment")
        assert response.status_code == 200
        assert 'rel="icon" href="http://testserver/static/favicon.svg"' in response.text
        assert 'id="camera-preview"' in response.text
        assert 'id="face-progress-ring"' in response.text
        assert 'role="progressbar"' in response.text
        assert 'id="front-progress"' in response.text
        assert 'id="overall-progress-percent"' in response.text
        assert 'id="face-progress-ring"' in response.text
        assert "오른쪽" in response.text
        assert "왼쪽" in response.text
        assert "수집 완료" in response.text
        assert 'id="camera-help"' in response.text
        assert 'id="capture-error"' in response.text
        assert "외부 문서" in response.text
        assert "SCRFD 얼굴 검출과 MediaPipe 자세 분석" in response.text

        favicon = client.get("/static/favicon.svg")
        assert favicon.status_code == 200
        assert favicon.headers["content-type"].startswith("image/svg+xml")

        legacy_favicon = client.get("/favicon.ico")
        assert legacy_favicon.status_code == 200
        assert legacy_favicon.url.path == "/static/favicon.svg"


def test_websocket_disconnect_discards_incomplete_session() -> None:
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/students/student-disconnect/face-enrollments",
            json={"consent_confirmed": True, "consent_confirmed_by": "admin"},
        )
        enrollment_id = created.json()["id"]
        with client.websocket_connect(
            f"/api/v1/face-enrollments/{enrollment_id}/frames"
        ) as websocket:
            websocket.send_bytes(b"NO_FACE")
            decision = websocket.receive_json()
            assert decision["accepted"] is False
        assert client.get(f"/api/v1/face-enrollments/{enrollment_id}").status_code == 404
