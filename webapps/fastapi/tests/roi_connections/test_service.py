"""ROI 연결 서비스 규칙 테스트."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.classrooms.adapters.memory_repository import InMemoryClassroomRepository
from app.classrooms.models import CreateClassroomCommand, CreateSeatCommand
from app.classrooms.service import ClassroomService
from app.roi_connections.adapters.memory import InMemoryRoiConnectionRepository
from app.roi_connections.errors import (
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
