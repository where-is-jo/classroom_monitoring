"""ROI 연결 서비스 규칙 테스트."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.classrooms.adapters.memory_repository import InMemoryClassroomRepository
from app.classrooms.models import CreateClassroomCommand, CreateSeatCommand
from app.classrooms.service import ClassroomService
from app.roi_connections.adapters.memory import InMemoryRoiConnectionRepository
from app.roi_connections.errors import (
    CameraFrameUnavailableError,
    RoiConnectionConflictError,
    RoiConnectionInputError,
    RoiConnectionNotFoundError,
)
from app.roi_connections.models import (
    Point,
    RoiConnection,
    SaveLiveRoiConnectionCommand,
    SaveRoiConnectionCommand,
)
from app.roi_connections.service import RoiConnectionService
from app.shared.adapters.memory_student_lookup import InMemoryStudentLookup
from app.shared.student_identity import StudentIdentity
from app.video_monitoring.adapters.memory_repository import MemoryVideoStreamRepository
from app.video_monitoring.models import PlaybackKind, VideoStream

from .fakes import JPEG_BYTES, FakeCameraFrameGrabber

NOW = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)


def _stream(camera_id: str, classroom_id: str = "room") -> VideoStream:
    return VideoStream(
        id=f"stream-{camera_id}",
        camera_id=camera_id,
        classroom_id=classroom_id,
        camera_label=f"{camera_id} 카메라",
        playback_kind=PlaybackKind.WEBRTC,
        playback_path=f"/webrtc/{camera_id}",
        enabled=True,
        last_frame_at=None,
        last_detection_at=None,
        is_demo=False,
        created_at=NOW,
        updated_at=NOW,
    )


def make_service(
    repository: InMemoryRoiConnectionRepository | None = None,
    grabber: FakeCameraFrameGrabber | None = None,
) -> RoiConnectionService:
    classroom_service = ClassroomService(
        InMemoryClassroomRepository(),
        occupancy_confidence_threshold=0.5,
        clock=lambda: NOW,
    )
    classroom_service.seed_classroom(
        CreateClassroomCommand(id="room", code="ROOM", name="테스트실", location="가상")
    )
    for seat_id in ("seat-a", "seat-b"):
        classroom_service.seed_seat(
            CreateSeatCommand(
                id=seat_id,
                classroom_id="room",
                code=seat_id.upper(),
                label=seat_id,
            )
        )
    students = InMemoryStudentLookup(
        (
            StudentIdentity(id="student-a", student_no="001", name="학생 A", is_active=True),
            StudentIdentity(id="student-b", student_no="002", name="학생 B", is_active=True),
        )
    )
    streams = MemoryVideoStreamRepository()
    streams.save(_stream("camera-a"))
    streams.save(_stream("camera-b"))
    return RoiConnectionService(
        classroom_service,
        students,
        repository or InMemoryRoiConnectionRepository(),
        streams,
        grabber or FakeCameraFrameGrabber({"camera-a", "camera-b"}),
        max_upload_bytes=1024,
        page_size_max=20,
        clock=lambda: NOW,
    )


def triangle() -> tuple[Point, ...]:
    return (Point(0.1, 0.1), Point(0.8, 0.1), Point(0.4, 0.8))


def _save_command(
    *,
    revision: int,
    camera_id: str = "camera-a",
    seat_id: str = "seat-a",
    student_id: str | None = "student-a",
    polygon: tuple[Point, ...] | None = None,
) -> SaveRoiConnectionCommand:
    return SaveRoiConnectionCommand(
        classroom_id="room",
        camera_id=camera_id,
        seat_id=seat_id,
        student_id=student_id,
        polygon=polygon or triangle(),
        reference_image_revision=revision,
    )


def test_image_revision_marks_only_same_camera_connection_for_review() -> None:
    service = make_service()
    image = service.save_reference_image(
        "room",
        "camera-a",
        content_type="image/png",
        content=b"\x89PNG\r\n\x1a\nfirst",
        filename="room.png",
    )
    service.save_connection(_save_command(revision=image.revision))

    replacement = service.save_reference_image(
        "room",
        "camera-a",
        content_type="image/png",
        content=b"\x89PNG\r\n\x1a\nnext",
        filename="next.png",
    )

    assert replacement.revision == 2
    assert service.list_connections("room", "camera-a")[0].needs_review is True


def test_reference_connection_requires_review_after_restart() -> None:
    repository = InMemoryRoiConnectionRepository()
    first_service = make_service(repository)
    image = first_service.save_reference_image(
        "room",
        "camera-a",
        content_type="image/png",
        content=b"\x89PNG\r\n\x1a\nfirst",
        filename="room.png",
    )
    first_service.save_connection(_save_command(revision=image.revision))

    restarted_service = make_service(repository)

    assert restarted_service.list_connections("room", "camera-a")[0].needs_review is True
    assert restarted_service.list_valid_connections("room", "camera-a") == []
    replacement = restarted_service.save_reference_image(
        "room",
        "camera-a",
        content_type="image/png",
        content=b"\x89PNG\r\n\x1a\nnext",
        filename="next.png",
    )
    assert replacement.revision == 2


def test_live_connection_remains_valid_after_restart() -> None:
    repository = InMemoryRoiConnectionRepository()
    first_service = make_service(repository)
    first_service.save_live_connection(
        SaveLiveRoiConnectionCommand(
            classroom_id="room",
            camera_id="camera-a",
            seat_id="seat-a",
            student_id="student-a",
            polygon=triangle(),
        )
    )

    restarted_service = make_service(repository)

    valid = restarted_service.list_valid_connections("room", "camera-a")
    assert len(valid) == 1
    assert valid[0].reference_image_revision == 0


def test_same_seat_can_have_different_camera_polygons() -> None:
    service = make_service()
    first = service.save_live_connection(
        SaveLiveRoiConnectionCommand("room", "camera-a", "seat-a", "student-a", triangle())
    )
    second = service.save_live_connection(
        SaveLiveRoiConnectionCommand("room", "camera-b", "seat-a", "student-a", triangle())
    )

    assert first.connection.camera_id == "camera-a"
    assert second.connection.camera_id == "camera-b"
    assert len(service.list_connections("room")) == 2


def test_student_change_preserves_polygon() -> None:
    service = make_service()
    image = service.save_reference_image(
        "room",
        "camera-a",
        content_type="image/jpeg",
        content=b"\xff\xd8\xffdata",
        filename="room.jpg",
    )
    service.save_connection(_save_command(revision=image.revision))

    changed = service.save_connection(
        _save_command(student_id="student-b", revision=image.revision)
    )

    assert changed.connection.student_id == "student-b"
    assert changed.connection.polygon == triangle()


def test_duplicate_student_is_rejected_within_same_camera() -> None:
    service = make_service()
    image = service.save_reference_image(
        "room",
        "camera-a",
        content_type="image/png",
        content=b"\x89PNG\r\n\x1a\nimage",
        filename="room.png",
    )
    service.save_connection(_save_command(revision=image.revision))

    with pytest.raises(RoiConnectionConflictError):
        service.save_connection(_save_command(seat_id="seat-b", revision=image.revision))


@pytest.mark.parametrize(
    "polygon",
    [
        (Point(0.1, 0.1), Point(0.2, 0.2)),
        (Point(0.1, 0.1), Point(1.2, 0.1), Point(0.4, 0.8)),
        (Point(0.1, 0.1), Point(0.8, 0.8), Point(0.1, 0.8), Point(0.8, 0.1)),
    ],
)
def test_invalid_polygon_is_rejected(polygon: tuple[Point, ...]) -> None:
    service = make_service()
    image = service.save_reference_image(
        "room",
        "camera-a",
        content_type="image/png",
        content=b"\x89PNG\r\n\x1a\nimage",
        filename="room.png",
    )

    with pytest.raises(RoiConnectionInputError):
        service.save_connection(
            _save_command(student_id=None, polygon=polygon, revision=image.revision)
        )


def test_spoofed_or_oversized_image_is_rejected() -> None:
    service = make_service()
    with pytest.raises(RoiConnectionInputError):
        service.save_reference_image(
            "room",
            "camera-a",
            content_type="image/png",
            content=b"not-png",
            filename="fake.png",
        )
    with pytest.raises(RoiConnectionInputError):
        service.save_reference_image(
            "room",
            "camera-a",
            content_type="image/jpeg",
            content=b"\xff\xd8\xff" + b"x" * 1024,
            filename="large.jpg",
        )


def test_camera_must_be_active_and_belong_to_classroom() -> None:
    service = make_service()

    with pytest.raises(RoiConnectionNotFoundError):
        service.save_live_connection(
            SaveLiveRoiConnectionCommand(
                "room", "camera-missing", "seat-a", "student-a", triangle()
            )
        )


def test_legacy_connection_is_visible_but_never_valid_for_camera() -> None:
    repository = InMemoryRoiConnectionRepository()
    repository.save(
        RoiConnection(
            classroom_id="room",
            camera_id=None,
            seat_id="seat-a",
            student_id="student-a",
            polygon=triangle(),
            reference_image_revision=0,
            updated_at=NOW,
        )
    )
    service = make_service(repository)

    all_connections = service.list_connections("room")

    assert len(all_connections) == 1
    assert all_connections[0].needs_review is True
    assert service.list_connections("room", "camera-a") == []
    assert service.list_valid_connections("room", "camera-a") == []


def test_capture_makes_a_reference_image_and_bumps_revision() -> None:
    grabber = FakeCameraFrameGrabber({"camera-a"})
    service = make_service(grabber=grabber)

    first = service.capture_reference_image("room", "camera-a")
    second = service.capture_reference_image("room", "camera-a")

    assert first.revision == 1
    assert second.revision == 2
    assert second.content_type == "image/jpeg"
    # 캡처할 때마다 카메라에 새로 붙는다. 연결을 붙들고 있지 않다.
    assert grabber.calls == ["camera-a", "camera-a"]


def test_capture_names_the_image_by_camera_and_time() -> None:
    """어느 카메라의 언제 화면인지 화면에서 알아볼 수 있어야 한다."""
    image = make_service().capture_reference_image("room", "camera-a")

    assert "camera-a 카메라" in image.display_name
    assert "20260814-090000" in image.display_name


def test_capture_rejects_a_camera_from_another_classroom() -> None:
    service = make_service()

    with pytest.raises(RoiConnectionNotFoundError):
        service.capture_reference_image("room", "camera-unknown")


def test_capture_rejects_a_frame_that_is_not_jpeg() -> None:
    """카메라가 이상한 것을 돌려줘도 기준 이미지로 삼지 않는다."""
    service = make_service(grabber=FakeCameraFrameGrabber({"camera-a"}, frame=b"not-an-image"))

    with pytest.raises(CameraFrameUnavailableError):
        service.capture_reference_image("room", "camera-a")


def test_capture_rejects_an_oversized_frame() -> None:
    oversized = JPEG_BYTES + b"x" * 4096
    service = make_service(grabber=FakeCameraFrameGrabber({"camera-a"}, frame=oversized))

    with pytest.raises(CameraFrameUnavailableError):
        service.capture_reference_image("room", "camera-a")


def test_recapture_marks_connections_drawn_on_the_old_frame_for_review() -> None:
    """다시 캡처하면 이전 화면 위의 ROI는 다른 화각일 수 있어 판정에서 빠진다."""
    service = make_service()
    revision = service.capture_reference_image("room", "camera-a").revision
    service.save_connection(_save_command(revision=revision))

    service.capture_reference_image("room", "camera-a")

    views = service.list_connections("room", "camera-a")
    assert [view.needs_review for view in views] == [True]
    assert service.list_valid_connections("room", "camera-a") == []


def test_camera_options_tell_the_page_which_camera_can_be_captured() -> None:
    service = make_service(grabber=FakeCameraFrameGrabber({"camera-a"}))

    options = {
        option.camera_id: option.capture_available for option in service.list_camera_options("room")
    }

    assert options == {"camera-a": True, "camera-b": False}


def test_deleting_a_roi_removes_the_seat_from_that_camera_only() -> None:
    """지운 좌석은 그 카메라의 관측에서만 빠진다. 다른 카메라의 ROI는 남는다."""
    service = make_service()
    revision_a = service.capture_reference_image("room", "camera-a").revision
    revision_b = service.capture_reference_image("room", "camera-b").revision
    service.save_connection(_save_command(revision=revision_a, camera_id="camera-a"))
    service.save_connection(
        _save_command(revision=revision_b, camera_id="camera-b", student_id=None)
    )

    service.delete_connection("room", "camera-a", "seat-a")

    assert service.list_connections("room", "camera-a") == []
    assert len(service.list_connections("room", "camera-b")) == 1


def test_deleting_a_missing_roi_raises() -> None:
    service = make_service()

    with pytest.raises(RoiConnectionNotFoundError):
        service.delete_connection("room", "camera-a", "seat-a")


def test_deleting_from_an_unknown_camera_raises() -> None:
    service = make_service()

    with pytest.raises(RoiConnectionNotFoundError):
        service.delete_connection("room", "camera-unknown", "seat-a")
