"""탐지 기반 ROI 자리 찾기·저장 서비스와 API 계약 테스트."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.classrooms.adapters.memory_repository import InMemoryClassroomRepository
from app.classrooms.models import CreateClassroomCommand, CreateSeatCommand
from app.classrooms.service import ClassroomService
from app.main import app
from app.roi_connections.adapters.memory import InMemoryRoiConnectionRepository
from app.roi_connections.detection_layout import DetectionSample
from app.roi_connections.errors import (
    RoiConnectionInputError,
    RoiConnectionNotFoundError,
)
from app.roi_connections.models import (
    ApplyDetectionRoiCommand,
    ConfirmAutoRoiCommand,
    DetectionRoiAssignment,
    DetectionRoiPlanResult,
    PlanDetectionRoiCommand,
)
from app.roi_connections.service import RoiConnectionService
from app.shared.adapters.memory_student_lookup import InMemoryStudentLookup
from app.shared.dependencies import get_roi_connection_service
from app.shared.student_identity import StudentIdentity
from app.video_monitoring.adapters.memory_repository import MemoryVideoStreamRepository
from app.video_monitoring.models import PlaybackKind, VideoStream

from .fakes import FakeCameraFrameGrabber, FakeSeatedDetectionSource

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def seated(track_id: str, x: float, y: float, *, count: int = 200) -> list[DetectionSample]:
    """한 자리에 앉아 있던 사람의 탐지. 창 안에 들어오도록 NOW 직전으로 만든다."""
    start = NOW - timedelta(seconds=count + 5)
    return [
        DetectionSample(
            x=x + (index % 5 - 2) * 0.004,
            y=y + (index % 5 - 2) * 0.002,
            track_id=track_id,
            captured_at=start + timedelta(seconds=index),
        )
        for index in range(count)
    ]


def make_service(
    samples: list[DetectionSample] | None = None,
    *,
    repository: InMemoryRoiConnectionRepository | None = None,
    source: FakeSeatedDetectionSource | None = None,
) -> RoiConnectionService:
    classroom_service = ClassroomService(
        InMemoryClassroomRepository(),
        occupancy_confidence_threshold=0.5,
        clock=lambda: NOW,
    )
    classroom_service.seed_classroom(
        CreateClassroomCommand(id="room", code="ROOM", name="테스트실", location="가상")
    )
    for index in range(1, 5):
        classroom_service.seed_seat(
            CreateSeatCommand(
                id=f"seat-{index}",
                classroom_id="room",
                code=f"S{index:02d}",
                label=f"좌석 {index}",
                row=1,
                column=index,
            )
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
        InMemoryStudentLookup(
            (StudentIdentity(id="student-a", student_no="001", name="학생 A", is_active=True),)
        ),
        repository or InMemoryRoiConnectionRepository(),
        streams,
        FakeCameraFrameGrabber({"camera-a"}),
        source or FakeSeatedDetectionSource(samples or []),
        max_upload_bytes=1024,
        page_size_max=20,
        clock=lambda: NOW,
    )


def plan(service: RoiConnectionService, *, hours: int = 24) -> DetectionRoiPlanResult:
    return service.plan_detection_rois(
        PlanDetectionRoiCommand(classroom_id="room", camera_id="camera-a", lookback_hours=hours)
    )


def test_plan_finds_spots_without_deciding_which_seat_they_are() -> None:
    """카메라는 자리를 알지만 좌석 이름을 알지 못한다. 추측해 붙이지 않는다."""
    service = make_service([*seated("p1", 0.25, 0.30), *seated("p2", 0.70, 0.60)])

    result = plan(service)

    assert len(result.proposals) == 2
    assert [proposal.index for proposal in result.proposals] == [1, 2]
    assert all(proposal.suggested_seat_id is None for proposal in result.proposals)
    assert all(proposal.sample_count > 0 for proposal in result.proposals)
    assert result.stationary_count > 0
    # 계획만 세운다. 저장은 하지 않는다.
    assert service.list_connections("room", "camera-a") == []


def test_plan_reads_only_the_requested_window() -> None:
    source = FakeSeatedDetectionSource(seated("p1", 0.25, 0.30))
    service = make_service(source=source)

    plan(service, hours=6)

    camera_id, since, until = source.calls[-1]
    assert camera_id == "camera-a"
    assert until == NOW
    assert until - since == timedelta(hours=6)


def test_applying_saves_the_chosen_seats_as_unconfirmed() -> None:
    service = make_service([*seated("p1", 0.25, 0.30), *seated("p2", 0.70, 0.60)])
    result = plan(service)

    saved = service.apply_detection_rois(
        ApplyDetectionRoiCommand(
            classroom_id="room",
            camera_id="camera-a",
            assignments=tuple(
                DetectionRoiAssignment(seat_id=f"seat-{proposal.index}", polygon=proposal.polygon)
                for proposal in result.proposals
            ),
        )
    )

    assert saved.saved_count == 2
    views = service.list_connections("room", "camera-a")
    assert len(views) == 2
    assert all(view.connection.auto_generated for view in views)
    assert all(view.needs_review for view in views)
    # 확정 전에는 좌석 판정에 쓰이지 않는다.
    assert service.list_valid_connections("room", "camera-a") == []


def test_saved_spots_survive_a_restart_because_they_do_not_need_a_captured_screen() -> None:
    """탐지 좌표의 근거는 캡처 화면이 아니다. 재시작해도 재검토로 떨어지지 않는다.

    격자 경로(결정 0039)는 기준 화면 revision에 매여 있어 프로세스를 다시 띄우면
    전부 재검토가 된다. 이 경로가 그 제약을 타지 않는 것이 핵심 차이다.
    """
    repository = InMemoryRoiConnectionRepository()
    service = make_service([*seated("p1", 0.25, 0.30)], repository=repository)
    result = plan(service)
    service.apply_detection_rois(
        ApplyDetectionRoiCommand(
            classroom_id="room",
            camera_id="camera-a",
            assignments=(
                DetectionRoiAssignment(seat_id="seat-1", polygon=result.proposals[0].polygon),
            ),
        )
    )
    # 확정은 격자 경로와 같은 명령을 그대로 쓴다.
    service.confirm_auto_connections(
        ConfirmAutoRoiCommand(classroom_id="room", camera_id="camera-a", seat_ids=None)
    )
    assert len(service.list_valid_connections("room", "camera-a")) == 1

    restarted = make_service([], repository=repository)

    assert len(restarted.list_valid_connections("room", "camera-a")) == 1


def test_existing_roi_is_suggested_when_regenerating() -> None:
    """다시 찾을 때 좌석을 처음부터 고르지 않게 한다."""
    service = make_service([*seated("p1", 0.25, 0.30)])
    first = plan(service)
    service.apply_detection_rois(
        ApplyDetectionRoiCommand(
            classroom_id="room",
            camera_id="camera-a",
            assignments=(
                DetectionRoiAssignment(seat_id="seat-3", polygon=first.proposals[0].polygon),
            ),
        )
    )

    second = plan(service)

    assert second.proposals[0].suggested_seat_id == "seat-3"


def test_applying_the_same_seat_twice_is_refused() -> None:
    service = make_service([*seated("p1", 0.25, 0.30), *seated("p2", 0.70, 0.60)])
    result = plan(service)

    with pytest.raises(RoiConnectionInputError):
        service.apply_detection_rois(
            ApplyDetectionRoiCommand(
                classroom_id="room",
                camera_id="camera-a",
                assignments=tuple(
                    DetectionRoiAssignment(seat_id="seat-1", polygon=proposal.polygon)
                    for proposal in result.proposals
                ),
            )
        )


def test_applying_to_an_unknown_seat_is_refused() -> None:
    service = make_service([*seated("p1", 0.25, 0.30)])
    result = plan(service)

    with pytest.raises(RoiConnectionNotFoundError):
        service.apply_detection_rois(
            ApplyDetectionRoiCommand(
                classroom_id="room",
                camera_id="camera-a",
                assignments=(
                    DetectionRoiAssignment(
                        seat_id="seat-none", polygon=result.proposals[0].polygon
                    ),
                ),
            )
        )


def test_applying_nothing_is_refused() -> None:
    service = make_service()

    with pytest.raises(RoiConnectionInputError):
        service.apply_detection_rois(
            ApplyDetectionRoiCommand(classroom_id="room", camera_id="camera-a", assignments=())
        )


def test_unknown_camera_is_refused() -> None:
    service = make_service()

    with pytest.raises(RoiConnectionNotFoundError):
        service.plan_detection_rois(
            PlanDetectionRoiCommand(classroom_id="room", camera_id="camera-none", lookback_hours=24)
        )


def test_no_detections_is_reported_as_an_empty_plan() -> None:
    """탐지가 없는 것은 오류가 아니다. 화면이 "아직 볼 것이 없다"고 말할 수 있어야 한다."""
    service = make_service([])

    result = plan(service)

    assert result.proposals == ()
    assert result.sample_count == 0


@pytest.fixture
def client() -> Iterator[TestClient]:
    service = make_service([*seated("p1", 0.25, 0.30), *seated("p2", 0.70, 0.60)])
    app.dependency_overrides[get_roi_connection_service] = lambda: service
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def test_api_find_then_assign_then_confirm(client: TestClient) -> None:
    found = client.post(
        "/api/v1/classrooms/room/roi-connections/auto/from-detections",
        json={"camera_id": "camera-a", "lookback_hours": 24},
    )
    assert found.status_code == 200
    body = found.json()
    assert len(body["proposals"]) == 2
    assert body["proposals"][0]["suggested_seat_id"] is None
    assert client.get("/api/v1/classrooms/room/roi-connections").json()["items"] == []

    applied = client.post(
        "/api/v1/classrooms/room/roi-connections/auto/from-detections/apply",
        json={
            "camera_id": "camera-a",
            "assignments": [
                {"seat_id": "seat-1", "polygon": body["proposals"][0]["polygon"]},
                {"seat_id": "seat-2", "polygon": body["proposals"][1]["polygon"]},
            ],
        },
    )
    assert applied.status_code == 200
    assert applied.json()["saved_count"] == 2

    listed = client.get("/api/v1/classrooms/room/roi-connections?camera_id=camera-a").json()
    assert len(listed["items"]) == 2
    assert all(item["auto_generated"] for item in listed["items"])
    assert all(item["reference_image_revision"] == 0 for item in listed["items"])

    confirmed = client.post(
        "/api/v1/classrooms/room/roi-connections/auto/confirm",
        json={"camera_id": "camera-a"},
    )
    assert confirmed.status_code == 200
    assert confirmed.json() == {"confirmed_count": 2, "stale_count": 0}


def test_api_rejects_a_lookback_beyond_the_limit(client: TestClient) -> None:
    response = client.post(
        "/api/v1/classrooms/room/roi-connections/auto/from-detections",
        json={"camera_id": "camera-a", "lookback_hours": 10_000},
    )

    assert response.status_code == 422


def test_api_rejects_a_polygon_with_too_few_points(client: TestClient) -> None:
    response = client.post(
        "/api/v1/classrooms/room/roi-connections/auto/from-detections/apply",
        json={
            "camera_id": "camera-a",
            "assignments": [
                {"seat_id": "seat-1", "polygon": [{"x": 0.1, "y": 0.1}, {"x": 0.2, "y": 0.2}]}
            ],
        },
    )

    assert response.status_code == 422


def test_page_offers_detection_controls(client: TestClient) -> None:
    response = client.get("/roi-connections?classroom_id=room")

    assert response.status_code == 200
    assert 'id="roi-detect"' in response.text
    assert 'id="roi-detect-save"' in response.text
    assert 'id="roi-detect-panel"' in response.text
    # 탐지 기간·좌석 크기는 화면에서 고르지 않는다. 서버 기본값을 쓴다.
    assert 'id="roi-lookback"' not in response.text
    assert 'id="roi-seat-fill"' not in response.text


def test_only_seat_judging_cameras_can_hold_seat_rois() -> None:
    """입구 카메라의 탐지는 좌석 판정에 참여하지 않는다(결정 0024의 3번).

    화면이 그 카메라를 기본으로 골라 사람이 지나다니기만 하는 화각에서 자리를 찾던
    문제를 막는다.
    """
    from app.video_monitoring.models import CameraRole

    service = make_service()
    streams = service._streams
    streams.save(
        VideoStream(
            id="stream-entry",
            camera_id="entry-camera",
            classroom_id="room",
            camera_label="입구 카메라",
            playback_kind=PlaybackKind.WEBRTC,
            playback_path="/webrtc/entry-camera",
            enabled=True,
            last_frame_at=None,
            last_detection_at=None,
            is_demo=False,
            created_at=NOW,
            updated_at=NOW,
            role=CameraRole.IDENTITY_ONLY,
        )
    )

    roi_cameras = [option.camera_id for option in service.list_roi_camera_options("room")]

    assert roi_cameras == ["camera-a"]
    # 일반 카메라 목록은 그대로다 — 신원 인계 화면이 입구 카메라를 찾는 데 쓴다.
    assert {option.camera_id for option in service.list_camera_options("room")} == {
        "camera-a",
        "entry-camera",
    }
