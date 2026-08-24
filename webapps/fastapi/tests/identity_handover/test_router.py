from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.shared.dependencies import get_identity_handover_route_service

from .helpers import make_service


@pytest.fixture
def client() -> Iterator[TestClient]:
    service, _ = make_service()
    app.dependency_overrides[get_identity_handover_route_service] = lambda: service
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def test_page_shows_two_camera_roles_and_visual_editor(client: TestClient) -> None:
    response = client.get("/identity-handover?classroom_id=room")

    assert response.status_code == 200
    assert 'id="handover-stage"' in response.text
    assert 'value="camera-01"' in response.text
    assert 'value="classroom-cctv"' in response.text
    assert "사람 bbox의 <strong>하단 중앙점</strong>" in response.text


def test_capture_save_list_and_worker_contract(client: TestClient) -> None:
    captured = client.post(
        "/api/v1/classrooms/room/identity-handover-reference-image/capture?camera_id=classroom-cctv"
    )
    assert captured.status_code == 201
    revision = captured.json()["revision"]
    assert client.get(captured.json()["image_url"]).content.startswith(b"\xff\xd8\xff")

    saved = client.put(
        "/api/v1/classrooms/room/identity-handover-routes/classroom-cctv",
        json={
            "entry_camera_id": "camera-01",
            "classroom_entry_zone": {
                "left": 0.6084,
                "top": 0.2321,
                "right": 0.8275,
                "bottom": 0.485,
            },
            "reference_image_revision": revision,
        },
    )
    assert saved.status_code == 200
    assert "IDENTITY_HANDOVER_ROUTES=" in saved.json()["worker_environment_value"]

    listed = client.get("/api/v1/classrooms/room/identity-handover-routes")
    assert listed.json()["items"][0]["classroom_camera_id"] == "classroom-cctv"
    worker = client.get("/internal/identity-handover-routes")
    assert worker.json() == {
        "items": [
            {
                "entry_camera_id": "camera-01",
                "classroom_camera_id": "classroom-cctv",
                "classroom_entry_zone": [0.6084, 0.2321, 0.8275, 0.485],
            }
        ]
    }


def test_delete_removes_route_from_worker_contract(client: TestClient) -> None:
    revision = client.post(
        "/api/v1/classrooms/room/identity-handover-reference-image/capture?camera_id=classroom-cctv"
    ).json()["revision"]
    client.put(
        "/api/v1/classrooms/room/identity-handover-routes/classroom-cctv",
        json={
            "entry_camera_id": "camera-01",
            "classroom_entry_zone": {
                "left": 0.6,
                "top": 0.2,
                "right": 0.8,
                "bottom": 0.5,
            },
            "reference_image_revision": revision,
        },
    )

    deleted = client.delete("/api/v1/classrooms/room/identity-handover-routes/classroom-cctv")

    assert deleted.status_code == 204
    assert client.get("/internal/identity-handover-routes").json() == {"items": []}
