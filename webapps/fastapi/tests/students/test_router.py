"""학생 라우터 테스트."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


class TestStudentAPI:
    def test_create_student(self) -> None:
        response = client.post(
            "/api/v1/students",
            json={"student_no": "20240001", "name": "김철수", "department": "컴퓨터공학과"},
        )
        assert response.status_code == 201
        assert "Location" in response.headers

    def test_list_students(self) -> None:
        response = client.get("/api/v1/students")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data

    def test_get_student(self) -> None:
        # 먼저 생성
        create_response = client.post(
            "/api/v1/students",
            json={"student_no": "20240002", "name": "이영희", "department": "소프트웨어학과"},
        )
        student_id = create_response.json()["id"]

        response = client.get(f"/api/v1/students/{student_id}")
        assert response.status_code == 200

    def test_update_student(self) -> None:
        create_response = client.post(
            "/api/v1/students",
            json={"student_no": "20240003", "name": "박지민", "department": "디자인학과"},
        )
        student_id = create_response.json()["id"]

        response = client.patch(
            f"/api/v1/students/{student_id}",
            json={"name": "박지민수"},
        )
        assert response.status_code == 200
        assert response.json()["name"] == "박지민수"

    def test_delete_student(self) -> None:
        create_response = client.post(
            "/api/v1/students",
            json={"student_no": "20240004", "name": "최수진", "department": "경영학과"},
        )
        student_id = create_response.json()["id"]

        response = client.delete(f"/api/v1/students/{student_id}")
        assert response.status_code == 204

    def test_duplicate_student_no(self) -> None:
        client.post(
            "/api/v1/students",
            json={"student_no": "20240005", "name": "정수정", "department": "영문학과"},
        )
        response = client.post(
            "/api/v1/students",
            json={"student_no": "20240005", "name": "다른사람", "department": "수학과"},
        )
        assert response.status_code == 409

    def test_get_nonexistent_student(self) -> None:
        response = client.get("/api/v1/students/nonexistent")
        assert response.status_code == 404
