"""ROI 연결 페이지와 API 계약 테스트."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.classrooms.adapters.memory_repository import InMemoryClassroomRepository
from app.classrooms.models import CreateClassroomCommand, CreateSeatCommand
from app.classrooms.service import ClassroomService
from app.main import app
from app.roi_connections.adapters.memory import InMemoryRoiConnectionRepository
from app.roi_connections.service import RoiConnectionService
from app.shared.adapters.memory_student_lookup import InMemoryStudentLookup
from app.shared.dependencies import get_roi_connection_service
from app.shared.student_identity import StudentIdentity
from app.video_monitoring.adapters.memory_repository import MemoryVideoStreamRepository
from app.video_monitoring.models import PlaybackKind, VideoStream

from .fakes import FakeCameraFrameGrabber


def make_service(
    *, seeded: bool = True, grabber: FakeCameraFrameGrabber | None = None
) -> RoiConnectionService:
    classroom_service = ClassroomService(
        InMemoryClassroomRepository(),
        occupancy_confidence_threshold=0.5,
        clock=lambda: datetime(2026, 8, 14, 9, 0, tzinfo=UTC),
    )
    if seeded:
        classroom_service.seed_classroom(
            CreateClassroomCommand(id="room", code="ROOM", name="테스트실", location="가상")
        )
        classroom_service.seed_seat(
            CreateSeatCommand(id="seat", classroom_id="room", code="S01", label="좌석 1")
        )
    students = InMemoryStudentLookup(
        (StudentIdentity(id="student", student_no="001", name="학생", is_active=True),)
    )
    streams = MemoryVideoStreamRepository()
    if seeded:
        streams.save(
            VideoStream(
                id="stream-camera-a",
                camera_id="camera-a",
                classroom_id="room",
                camera_label="전면 카메라",
                playback_kind=PlaybackKind.WEBRTC,
                playback_path="/webrtc/camera-a",
                enabled=True,
                last_frame_at=None,
                last_detection_at=None,
                is_demo=False,
                created_at=datetime(2026, 8, 14, 9, 0, tzinfo=UTC),
                updated_at=datetime(2026, 8, 14, 9, 0, tzinfo=UTC),
            )
        )
    return RoiConnectionService(
        classroom_service,
        students,
        InMemoryRoiConnectionRepository(),
        streams,
        grabber or FakeCameraFrameGrabber(),
        max_upload_bytes=1024,
        page_size_max=20,
        clock=lambda: datetime(2026, 8, 14, 9, 0, tzinfo=UTC),
    )


@pytest.fixture
def client() -> Iterator[TestClient]:
    service = make_service()
    app.dependency_overrides[get_roi_connection_service] = lambda: service
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def test_page_renders_live_editor_controls_and_student_modal(client: TestClient) -> None:
    response = client.get("/roi-connections?classroom_id=room")
    assert response.status_code == 200
    assert 'id="roi-start"' in response.text
    assert 'id="roi-finish"' in response.text
    assert 'id="roi-reset" type="button" class="secondary" disabled' in response.text
    assert 'id="roi-cancel" type="button" class="secondary" disabled' in response.text
    assert 'id="roi-classroom-select"' in response.text
    assert 'id="roi-camera-select"' in response.text
    assert 'value="camera-a"' in response.text
    assert '<dialog id="roi-student-dialog"' in response.text
    assert 'id="roi-seat-select"' in response.text
    assert 'value="seat"' in response.text
    assert 'id="roi-image-form"' not in response.text


def test_page_does_not_add_hardcoded_test_classroom() -> None:
    app.dependency_overrides[get_roi_connection_service] = lambda: make_service(seeded=False)
    try:
        with TestClient(app) as test_client:
            response = test_client.get("/roi-connections")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert "테스트 강의실" not in response.text


def test_page_marks_camera_without_connection_details() -> None:
    """접속 정보가 없는 카메라를 캡처할 수 있는 것처럼 보이지 않게 한다."""
    service = make_service(grabber=FakeCameraFrameGrabber(set()))
    app.dependency_overrides[get_roi_connection_service] = lambda: service
    try:
        with TestClient(app) as test_client:
            response = test_client.get("/roi-connections?classroom_id=room")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert 'data-capture-available="false"' in response.text
    assert "접속 정보 없음" in response.text


def test_capture_creates_reference_image_and_serves_it(client: TestClient) -> None:
    captured = client.post("/api/v1/classrooms/room/roi-reference-image/capture?camera_id=camera-a")

    assert captured.status_code == 201
    body = captured.json()
    assert body["camera_id"] == "camera-a"
    assert body["revision"] == 1
    assert body["image_url"] == "/api/v1/classrooms/room/roi-reference-image?camera_id=camera-a"

    served = client.get(body["image_url"])
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/jpeg"
    assert served.headers["cache-control"] == "no-store"


def test_capture_failure_is_reported_as_upstream_error() -> None:
    """캡처 실패는 502다. 이 앱이 아니라 카메라 쪽 문제이기 때문이다."""
    service = make_service(grabber=FakeCameraFrameGrabber(fail=True))
    app.dependency_overrides[get_roi_connection_service] = lambda: service
    try:
        with TestClient(app) as test_client:
            response = test_client.post(
                "/api/v1/classrooms/room/roi-reference-image/capture?camera_id=camera-a"
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "CAMERA_FRAME_UNAVAILABLE"


def test_capture_then_save_roi_on_that_frame(client: TestClient) -> None:
    revision = client.post(
        "/api/v1/classrooms/room/roi-reference-image/capture?camera_id=camera-a"
    ).json()["revision"]

    saved = client.put(
        "/api/v1/classrooms/room/seats/seat/roi-connection",
        json={
            "camera_id": "camera-a",
            "student_id": "student",
            "polygon": [{"x": 0.1, "y": 0.1}, {"x": 0.8, "y": 0.1}, {"x": 0.4, "y": 0.8}],
            "reference_image_revision": revision,
        },
    )

    assert saved.status_code == 200
    assert saved.json()["needs_review"] is False


def test_roi_saved_on_an_older_frame_is_rejected(client: TestClient) -> None:
    """다시 캡처하면 이전 화면 위의 좌표는 다른 화각일 수 있으므로 거절한다."""
    stale = client.post(
        "/api/v1/classrooms/room/roi-reference-image/capture?camera_id=camera-a"
    ).json()["revision"]
    client.post("/api/v1/classrooms/room/roi-reference-image/capture?camera_id=camera-a")

    rejected = client.put(
        "/api/v1/classrooms/room/seats/seat/roi-connection",
        json={
            "camera_id": "camera-a",
            "student_id": "student",
            "polygon": [{"x": 0.1, "y": 0.1}, {"x": 0.8, "y": 0.1}, {"x": 0.4, "y": 0.8}],
            "reference_image_revision": stale,
        },
    )

    assert rejected.status_code == 409


def test_live_roi_connection_saves_classroom_student_and_polygon(client: TestClient) -> None:
    saved = client.put(
        "/api/v1/classrooms/room/roi-connection",
        json={
            "camera_id": "camera-a",
            "seat_id": "seat",
            "student_id": "student",
            "polygon": [
                {"x": 0.1, "y": 0.1},
                {"x": 0.8, "y": 0.1},
                {"x": 0.4, "y": 0.8},
            ],
        },
    )
    assert saved.status_code == 200
    assert saved.json()["classroom_id"] == "room"
    assert saved.json()["camera_id"] == "camera-a"
    assert saved.json()["student_id"] == "student"
    assert saved.json()["seat_id"] == "seat"


def test_live_roi_connection_requires_camera_and_rejects_wrong_scope(
    client: TestClient,
) -> None:
    payload = {
        "seat_id": "seat",
        "student_id": "student",
        "polygon": [
            {"x": 0.1, "y": 0.1},
            {"x": 0.8, "y": 0.1},
            {"x": 0.4, "y": 0.8},
        ],
    }

    missing = client.put("/api/v1/classrooms/room/roi-connection", json=payload)
    wrong = client.put(
        "/api/v1/classrooms/room/roi-connection",
        json={**payload, "camera_id": "camera-missing"},
    )

    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "VALIDATION_ERROR"
    assert wrong.status_code == 404
    assert wrong.json()["error"]["code"] == "ROI_CONNECTION_NOT_FOUND"


def test_upload_read_and_save_connection(client: TestClient) -> None:
    upload = client.post(
        "/api/v1/classrooms/room/roi-reference-image?camera_id=camera-a",
        files={"image": ("room.png", b"\x89PNG\r\n\x1a\nimage", "image/png")},
    )
    assert upload.status_code == 201
    assert upload.json()["revision"] == 1

    image = client.get("/api/v1/classrooms/room/roi-reference-image?camera_id=camera-a")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"

    saved = client.put(
        "/api/v1/classrooms/room/seats/seat/roi-connection",
        json={
            "camera_id": "camera-a",
            "student_id": "student",
            "polygon": [{"x": 0.1, "y": 0.1}, {"x": 0.8, "y": 0.1}, {"x": 0.4, "y": 0.8}],
            "reference_image_revision": 1,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["student_id"] == "student"

    listed = client.get("/api/v1/classrooms/room/roi-connections?camera_id=camera-a")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["seat_id"] == "seat"


def test_invalid_upload_and_stale_revision_errors(client: TestClient) -> None:
    invalid = client.post(
        "/api/v1/classrooms/room/roi-reference-image?camera_id=camera-a",
        files={"image": ("fake.png", b"not-image", "image/png")},
    )
    assert invalid.status_code == 422

    for suffix in (b"first", b"second"):
        response = client.post(
            "/api/v1/classrooms/room/roi-reference-image?camera_id=camera-a",
            files={"image": ("room.png", b"\x89PNG\r\n\x1a\n" + suffix, "image/png")},
        )
        assert response.status_code == 201

    stale = client.put(
        "/api/v1/classrooms/room/seats/seat/roi-connection",
        json={
            "camera_id": "camera-a",
            "student_id": None,
            "polygon": [{"x": 0.1, "y": 0.1}, {"x": 0.8, "y": 0.1}, {"x": 0.4, "y": 0.8}],
            "reference_image_revision": 1,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "ROI_CONNECTION_CONFLICT"


def test_saved_roi_can_be_deleted(client: TestClient) -> None:
    revision = client.post(
        "/api/v1/classrooms/room/roi-reference-image/capture?camera_id=camera-a"
    ).json()["revision"]
    client.put(
        "/api/v1/classrooms/room/seats/seat/roi-connection",
        json={
            "camera_id": "camera-a",
            "student_id": "student",
            "polygon": [{"x": 0.1, "y": 0.1}, {"x": 0.8, "y": 0.1}, {"x": 0.4, "y": 0.8}],
            "reference_image_revision": revision,
        },
    )

    deleted = client.delete("/api/v1/classrooms/room/seats/seat/roi-connection?camera_id=camera-a")

    assert deleted.status_code == 204
    listed = client.get("/api/v1/classrooms/room/roi-connections?camera_id=camera-a")
    assert listed.json()["items"] == []


def test_deleting_a_missing_roi_is_reported_not_silently_ignored(client: TestClient) -> None:
    """지울 것이 없었다는 사실을 알려야 관리자가 화면을 새로고침할 수 있다."""
    response = client.delete("/api/v1/classrooms/room/seats/seat/roi-connection?camera_id=camera-a")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ROI_CONNECTION_NOT_FOUND"


def test_delete_requires_camera_scope(client: TestClient) -> None:
    """같은 좌석이라도 카메라마다 다른 ROI를 갖는다. 어느 화각인지 지정해야 한다."""
    missing_camera = client.delete("/api/v1/classrooms/room/seats/seat/roi-connection")

    assert missing_camera.status_code == 422
