from __future__ import annotations

import pytest

from app.identity_handover.errors import IdentityHandoverRouteInputError
from app.identity_handover.models import (
    HandoverZone,
    SaveIdentityHandoverRouteCommand,
)
from app.video_monitoring.models import CameraRole

from .helpers import make_service, stream


def command(revision: int, *, zone: HandoverZone | None = None) -> SaveIdentityHandoverRouteCommand:
    return SaveIdentityHandoverRouteCommand(
        classroom_id="room",
        entry_camera_id="camera-01",
        classroom_camera_id="classroom-cctv",
        classroom_entry_zone=zone or HandoverZone(0.60, 0.23, 0.83, 0.49),
        reference_image_revision=revision,
    )


def test_capture_and_save_route_for_the_two_camera_roles() -> None:
    service, _ = make_service()

    image = service.capture_reference_image("room", "classroom-cctv")
    saved = service.save_route(command(image.revision))

    assert saved.entry_camera_id == "camera-01"
    assert saved.classroom_camera_id == "classroom-cctv"
    assert saved.classroom_entry_zone.as_tuple() == (0.60, 0.23, 0.83, 0.49)
    assert service.list_active_routes() == [saved]


def test_page_options_split_identity_and_tracking_cameras() -> None:
    service, _ = make_service()

    options = service.page_options("room")

    assert [item.camera_id for item in options.entry_cameras] == ["camera-01"]
    assert [item.camera_id for item in options.classroom_cameras] == ["classroom-cctv"]
    assert options.classroom_cameras[0].capture_available is True


def test_save_rejects_stale_reference_frame() -> None:
    service, _ = make_service()
    stale = service.capture_reference_image("room", "classroom-cctv").revision
    service.capture_reference_image("room", "classroom-cctv")

    with pytest.raises(IdentityHandoverRouteInputError, match="바뀌었습니다"):
        service.save_route(command(stale))


@pytest.mark.parametrize(
    "zone",
    [HandoverZone(0.8, 0.2, 0.4, 0.5), HandoverZone(-0.1, 0.2, 0.4, 0.5)],
)
def test_save_rejects_invalid_rectangle(zone: HandoverZone) -> None:
    service, _ = make_service()
    revision = service.capture_reference_image("room", "classroom-cctv").revision

    with pytest.raises(IdentityHandoverRouteInputError):
        service.save_route(command(revision, zone=zone))


def test_route_is_not_active_after_camera_role_changes() -> None:
    service, streams = make_service()
    revision = service.capture_reference_image("room", "classroom-cctv").revision
    service.save_route(command(revision))
    streams.save(stream("classroom-cctv", CameraRole.IDENTITY_ONLY))

    assert service.list_active_routes() == []


def test_wrong_camera_roles_are_rejected() -> None:
    service, _ = make_service()
    revision = service.capture_reference_image("room", "classroom-cctv").revision

    with pytest.raises(IdentityHandoverRouteInputError, match="입구 카메라"):
        service.save_route(
            SaveIdentityHandoverRouteCommand(
                "room",
                "classroom-cctv",
                "camera-01",
                HandoverZone(0.1, 0.1, 0.3, 0.4),
                revision,
            )
        )
