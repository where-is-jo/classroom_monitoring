"""ROI 연결 서비스 규칙 테스트."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.classrooms.adapters.memory_repository import InMemoryClassroomRepository
from app.classrooms.models import CreateClassroomCommand, CreateSeatCommand
from app.classrooms.service import ClassroomService
from app.roi_connections.adapters.memory import InMemoryRoiConnectionRepository
from app.roi_connections.errors import RoiConnectionConflictError, RoiConnectionInputError
from app.roi_connections.models import (
    Point,
    SaveLiveRoiConnectionCommand,
    SaveRoiConnectionCommand,
)
from app.roi_connections.service import RoiConnectionService
from app.shared.adapters.memory_student_lookup import InMemoryStudentLookup
from app.shared.student_identity import StudentIdentity

NOW = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)


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
    return RoiConnectionService(
        classroom_service,
        students,
        repository or InMemoryRoiConnectionRepository(),
        max_upload_bytes=1024,
        page_size_max=20,
        clock=lambda: NOW,
    )


def triangle() -> tuple[Point, ...]:
    return (Point(0.1, 0.1), Point(0.8, 0.1), Point(0.4, 0.8))


def test_image_revision_marks_previous_connection_for_review() -> None:
    service = make_service()
    image = service.save_reference_image(
        "room", content_type="image/png", content=b"\x89PNG\r\n\x1a\nfirst", filename="room.png"
    )
    service.save_connection(
        SaveRoiConnectionCommand("room", "seat-a", "student-a", triangle(), image.revision)
    )

    replacement = service.save_reference_image(
        "room", content_type="image/png", content=b"\x89PNG\r\n\x1a\nnext", filename="next.png"
    )

    assert replacement.revision == 2
    assert service.list_connections("room")[0].needs_review is True


def test_connection_survives_service_restart_and_new_image_revision_advances() -> None:
    repository = InMemoryRoiConnectionRepository()
    first_service = make_service(repository)
    image = first_service.save_reference_image(
        "room", content_type="image/png", content=b"\x89PNG\r\n\x1a\nfirst", filename="room.png"
    )
    first_service.save_connection(
        SaveRoiConnectionCommand("room", "seat-a", "student-a", triangle(), image.revision)
    )

    restarted_service = make_service(repository)

    assert restarted_service.list_connections("room")[0].needs_review is True
    replacement = restarted_service.save_reference_image(
        "room", content_type="image/png", content=b"\x89PNG\r\n\x1a\nnext", filename="next.png"
    )
    assert replacement.revision == 2


def test_student_change_preserves_polygon() -> None:
    service = make_service()
    image = service.save_reference_image(
        "room", content_type="image/jpeg", content=b"\xff\xd8\xffdata", filename="room.jpg"
    )
    service.save_connection(
        SaveRoiConnectionCommand("room", "seat-a", "student-a", triangle(), image.revision)
    )

    changed = service.save_connection(
        SaveRoiConnectionCommand("room", "seat-a", "student-b", triangle(), image.revision)
    )

    assert changed.connection.student_id == "student-b"
    assert changed.connection.polygon == triangle()


def test_duplicate_student_is_rejected() -> None:
    service = make_service()
    image = service.save_reference_image(
        "room", content_type="image/png", content=b"\x89PNG\r\n\x1a\nimage", filename="room.png"
    )
    service.save_connection(
        SaveRoiConnectionCommand("room", "seat-a", "student-a", triangle(), image.revision)
    )

    with pytest.raises(RoiConnectionConflictError):
        service.save_connection(
            SaveRoiConnectionCommand("room", "seat-b", "student-a", triangle(), image.revision)
        )


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
        "room", content_type="image/png", content=b"\x89PNG\r\n\x1a\nimage", filename="room.png"
    )

    with pytest.raises(RoiConnectionInputError):
        service.save_connection(
            SaveRoiConnectionCommand("room", "seat-a", None, polygon, image.revision)
        )


def test_spoofed_or_oversized_image_is_rejected() -> None:
    service = make_service()
    with pytest.raises(RoiConnectionInputError):
        service.save_reference_image(
            "room", content_type="image/png", content=b"not-png", filename="fake.png"
        )
    with pytest.raises(RoiConnectionInputError):
        service.save_reference_image(
            "room",
            content_type="image/jpeg",
            content=b"\xff\xd8\xff" + b"x" * 1024,
            filename="large.jpg",
        )


def test_live_connection_uses_student_based_temporary_seat_key() -> None:
    service = make_service()

    saved = service.save_live_connection(
        SaveLiveRoiConnectionCommand("room", "seat-a", "student-a", triangle())
    )

    assert saved.connection.classroom_id == "room"
    assert saved.connection.student_id == "student-a"
    assert saved.connection.seat_id == "seat-a"
    assert saved.connection.polygon == triangle()
