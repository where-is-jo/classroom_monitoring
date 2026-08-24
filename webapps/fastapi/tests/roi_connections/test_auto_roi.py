"""좌석 격자 기반 ROI 자동 생성 서비스·API 계약 테스트.

카메라 대역(`FakeCameraFrameGrabber`)을 쓰므로 실제 장비 없이 돈다.
"""

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
from app.roi_connections.errors import (
    RoiConnectionConflictError,
    RoiConnectionInputError,
    RoiConnectionNotFoundError,
)
from app.roi_connections.models import (
    AutoRoiOutcome,
    AutoRoiResult,
    ConfirmAutoRoiCommand,
    GenerateAutoRoiCommand,
    Point,
    SaveRoiConnectionCommand,
)
from app.roi_connections.service import RoiConnectionService
from app.shared.adapters.memory_student_lookup import InMemoryStudentLookup
from app.shared.dependencies import get_roi_connection_service
from app.shared.student_identity import StudentIdentity
from app.video_monitoring.adapters.memory_repository import MemoryVideoStreamRepository
from app.video_monitoring.models import PlaybackKind, VideoStream

from .fakes import FakeCameraFrameGrabber

NOW = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)

# 화면 전체를 좌석 구역으로 잡는다. 1행 1열 바깥 모서리부터 이웃한 순서다.
FULL_FRAME_CORNERS = (Point(0, 0), Point(1, 0), Point(1, 1), Point(0, 1))


def make_service(
    *,
    rows: int = 2,
    columns: int = 2,
    with_grid: bool = True,
) -> RoiConnectionService:
    classroom_service = ClassroomService(
        InMemoryClassroomRepository(),
        occupancy_confidence_threshold=0.5,
        clock=lambda: NOW,
    )
    classroom_service.seed_classroom(
        CreateClassroomCommand(id="room", code="ROOM", name="테스트실", location="가상")
    )
    for row in range(1, rows + 1):
        for column in range(1, columns + 1):
            classroom_service.seed_seat(
                CreateSeatCommand(
                    id=f"seat-{row}-{column}",
                    classroom_id="room",
                    code=f"S{row}{column}",
                    label=f"{row}행 {column}열",
                    row=row if with_grid else None,
                    column=column if with_grid else None,
                )
            )
    students = InMemoryStudentLookup(
        (StudentIdentity(id="student-a", student_no="001", name="학생 A", is_active=True),)
    )
    streams = MemoryVideoStreamRepository()
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
            created_at=NOW,
            updated_at=NOW,
        )
    )
    return RoiConnectionService(
        classroom_service,
        students,
        InMemoryRoiConnectionRepository(),
        streams,
        FakeCameraFrameGrabber({"camera-a"}),
        max_upload_bytes=1024,
        page_size_max=20,
        clock=lambda: NOW,
    )


def capture(service: RoiConnectionService) -> int:
    return service.capture_reference_image("room", "camera-a").revision


def generate(
    service: RoiConnectionService,
    *,
    revision: int,
    dry_run: bool = False,
    fill: float = 0.8,
    corners: tuple[Point, ...] = FULL_FRAME_CORNERS,
) -> AutoRoiResult:
    return service.generate_auto_connections(
        GenerateAutoRoiCommand(
            classroom_id="room",
            camera_id="camera-a",
            corners=corners,
            reference_image_revision=revision,
            seat_fill_ratio=fill,
            dry_run=dry_run,
        )
    )


def test_auto_generation_creates_one_roi_per_grid_seat() -> None:
    service = make_service()
    revision = capture(service)

    result = generate(service, revision=revision)

    assert result.grid_rows == 2
    assert result.grid_columns == 2
    assert result.generated_count == 4
    assert result.skipped_count == 0
    assert [seat.seat_id for seat in result.seats] == [
        "seat-1-1",
        "seat-1-2",
        "seat-2-1",
        "seat-2-2",
    ]
    saved = service.list_connections("room", "camera-a")
    assert len(saved) == 4
    assert all(view.connection.polygon for view in saved)


def test_generated_rois_do_not_take_part_in_judgement_until_confirmed() -> None:
    """계산으로 만든 좌표가 조용히 출결 판정에 들어가면 안 된다(결정 0020의 6번)."""
    service = make_service()
    revision = capture(service)

    generate(service, revision=revision)

    views = service.list_connections("room", "camera-a")
    assert all(view.needs_review for view in views)
    assert all(view.connection.auto_generated for view in views)
    assert service.list_valid_connections("room", "camera-a") == []


def test_confirming_puts_generated_rois_into_judgement() -> None:
    service = make_service()
    revision = capture(service)
    generate(service, revision=revision)

    result = service.confirm_auto_connections(
        ConfirmAutoRoiCommand(classroom_id="room", camera_id="camera-a", seat_ids=None)
    )

    assert result.confirmed_count == 4
    assert result.stale_count == 0
    assert len(service.list_valid_connections("room", "camera-a")) == 4
    assert not any(view.needs_review for view in service.list_connections("room", "camera-a"))


def test_confirming_only_some_seats_leaves_the_rest_out_of_judgement() -> None:
    service = make_service()
    revision = capture(service)
    generate(service, revision=revision)

    result = service.confirm_auto_connections(
        ConfirmAutoRoiCommand(
            classroom_id="room", camera_id="camera-a", seat_ids=("seat-1-1", "seat-2-2")
        )
    )

    assert result.confirmed_count == 2
    confirmed = {
        connection.seat_id for connection in service.list_valid_connections("room", "camera-a")
    }
    assert confirmed == {"seat-1-1", "seat-2-2"}


def test_recaptured_screen_blocks_confirmation_instead_of_pretending() -> None:
    """다른 화각 위에서 만든 좌표를 확정한 것처럼 보고하지 않는다."""
    service = make_service()
    revision = capture(service)
    generate(service, revision=revision)

    capture(service)  # 화면을 다시 잡는다 = 좌표의 근거가 바뀐다
    result = service.confirm_auto_connections(
        ConfirmAutoRoiCommand(classroom_id="room", camera_id="camera-a", seat_ids=None)
    )

    assert result.confirmed_count == 0
    assert result.stale_count == 4
    assert service.list_valid_connections("room", "camera-a") == []


def test_dry_run_calculates_without_saving() -> None:
    service = make_service()
    revision = capture(service)

    result = generate(service, revision=revision, dry_run=True)

    assert result.dry_run is True
    assert result.generated_count == 4
    assert all(seat.polygon is not None for seat in result.seats)
    assert service.list_connections("room", "camera-a") == []


def test_manually_drawn_roi_is_never_replaced() -> None:
    service = make_service()
    revision = capture(service)
    service.save_connection(
        SaveRoiConnectionCommand(
            classroom_id="room",
            camera_id="camera-a",
            seat_id="seat-1-1",
            student_id="student-a",
            polygon=(Point(0.01, 0.01), Point(0.09, 0.01), Point(0.09, 0.09)),
            reference_image_revision=revision,
        )
    )

    result = generate(service, revision=revision)

    outcomes = {seat.seat_id: seat.outcome for seat in result.seats}
    assert outcomes["seat-1-1"] is AutoRoiOutcome.EXISTING_KEPT
    assert result.generated_count == 3
    manual = next(
        view
        for view in service.list_connections("room", "camera-a")
        if view.connection.seat_id == "seat-1-1"
    )
    assert manual.connection.polygon == (Point(0.01, 0.01), Point(0.09, 0.01), Point(0.09, 0.09))
    assert manual.connection.student_id == "student-a"
    assert manual.connection.auto_generated is False


def test_regenerating_replaces_previous_proposals() -> None:
    """모서리나 좌석 크기를 고쳐 다시 만드는 것이 정상적인 사용 방식이다."""
    service = make_service()
    revision = capture(service)
    first = generate(service, revision=revision, fill=0.9)

    second = generate(service, revision=revision, fill=0.4)

    assert second.generated_count == 4
    assert len(service.list_connections("room", "camera-a")) == 4
    first_polygon = next(seat.polygon for seat in first.seats if seat.seat_id == "seat-1-1")
    second_polygon = next(seat.polygon for seat in second.seats if seat.seat_id == "seat-1-1")
    assert first_polygon != second_polygon


def test_confirmed_rois_are_treated_as_manual_afterwards() -> None:
    service = make_service()
    revision = capture(service)
    generate(service, revision=revision)
    service.confirm_auto_connections(
        ConfirmAutoRoiCommand(classroom_id="room", camera_id="camera-a", seat_ids=None)
    )

    result = generate(service, revision=revision)

    assert result.generated_count == 0
    assert all(seat.outcome is AutoRoiOutcome.EXISTING_KEPT for seat in result.seats)


def test_generation_requires_the_current_reference_screen() -> None:
    service = make_service()
    revision = capture(service)

    with pytest.raises(RoiConnectionConflictError):
        generate(service, revision=revision + 1)


def test_generation_without_any_reference_screen_is_refused() -> None:
    service = make_service()

    with pytest.raises(RoiConnectionConflictError):
        generate(service, revision=1)


def test_seats_without_grid_coordinates_cannot_be_generated() -> None:
    service = make_service(with_grid=False)
    revision = capture(service)

    with pytest.raises(RoiConnectionInputError):
        generate(service, revision=revision)


def test_unknown_camera_is_refused() -> None:
    service = make_service()
    capture(service)

    with pytest.raises(RoiConnectionNotFoundError):
        service.generate_auto_connections(
            GenerateAutoRoiCommand(
                classroom_id="room",
                camera_id="camera-none",
                corners=FULL_FRAME_CORNERS,
                reference_image_revision=1,
                seat_fill_ratio=0.8,
                dry_run=True,
            )
        )


def test_confirming_when_there_is_nothing_generated_is_reported() -> None:
    service = make_service()
    capture(service)

    with pytest.raises(RoiConnectionNotFoundError):
        service.confirm_auto_connections(
            ConfirmAutoRoiCommand(classroom_id="room", camera_id="camera-a", seat_ids=None)
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


def _corner_payload() -> list[dict[str, float]]:
    return [{"x": corner.x, "y": corner.y} for corner in FULL_FRAME_CORNERS]


def test_api_preview_then_save_round_trip(client: TestClient) -> None:
    capture_response = client.post(
        "/api/v1/classrooms/room/roi-reference-image/capture?camera_id=camera-a"
    )
    assert capture_response.status_code == 201
    revision = capture_response.json()["revision"]

    preview = client.post(
        "/api/v1/classrooms/room/roi-connections/auto",
        json={
            "camera_id": "camera-a",
            "corners": _corner_payload(),
            "reference_image_revision": revision,
            "dry_run": True,
        },
    )
    assert preview.status_code == 200
    body = preview.json()
    assert body["generated_count"] == 4
    assert body["grid_rows"] == 2 and body["grid_columns"] == 2
    assert len(body["seats"][0]["polygon"]) == 4
    assert client.get("/api/v1/classrooms/room/roi-connections").json()["items"] == []

    saved = client.post(
        "/api/v1/classrooms/room/roi-connections/auto",
        json={
            "camera_id": "camera-a",
            "corners": _corner_payload(),
            "reference_image_revision": revision,
            "dry_run": False,
        },
    )
    assert saved.status_code == 200
    listed = client.get("/api/v1/classrooms/room/roi-connections?camera_id=camera-a").json()
    assert len(listed["items"]) == 4
    assert all(item["auto_generated"] for item in listed["items"])
    assert all(item["needs_review"] for item in listed["items"])

    confirmed = client.post(
        "/api/v1/classrooms/room/roi-connections/auto/confirm",
        json={"camera_id": "camera-a"},
    )
    assert confirmed.status_code == 200
    assert confirmed.json() == {"confirmed_count": 4, "stale_count": 0}
    listed = client.get("/api/v1/classrooms/room/roi-connections?camera_id=camera-a").json()
    assert not any(item["needs_review"] for item in listed["items"])


def test_api_rejects_a_corner_count_other_than_four(client: TestClient) -> None:
    response = client.post(
        "/api/v1/classrooms/room/roi-connections/auto",
        json={
            "camera_id": "camera-a",
            "corners": _corner_payload()[:3],
            "reference_image_revision": 1,
        },
    )

    assert response.status_code == 422


def test_api_reports_a_self_crossing_area_as_input_error(client: TestClient) -> None:
    capture_response = client.post(
        "/api/v1/classrooms/room/roi-reference-image/capture?camera_id=camera-a"
    )
    revision = capture_response.json()["revision"]

    response = client.post(
        "/api/v1/classrooms/room/roi-connections/auto",
        json={
            "camera_id": "camera-a",
            "corners": [
                {"x": 0.1, "y": 0.1},
                {"x": 0.9, "y": 0.1},
                {"x": 0.1, "y": 0.9},
                {"x": 0.9, "y": 0.9},
            ],
            "reference_image_revision": revision,
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "ROI_CONNECTION_INPUT_INVALID"


def test_page_offers_auto_generation_controls(client: TestClient) -> None:
    response = client.get("/roi-connections?classroom_id=room")

    assert response.status_code == 200
    assert 'id="roi-auto"' in response.text
    assert 'id="roi-auto-save"' in response.text
    assert 'id="roi-auto-confirm"' in response.text
    assert 'id="roi-seat-fill"' in response.text
    assert 'id="roi-auto-preview"' in response.text
    # 기준 화면이 없으면 자동 생성을 시작할 수 없다.
    assert 'id="roi-auto" type="button" disabled' in response.text
