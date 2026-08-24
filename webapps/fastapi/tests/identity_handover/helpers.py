from __future__ import annotations

from datetime import UTC, datetime

from app.classrooms.adapters.memory_repository import InMemoryClassroomRepository
from app.classrooms.models import CreateClassroomCommand
from app.classrooms.service import ClassroomService
from app.identity_handover.adapters.memory import (
    InMemoryIdentityHandoverRouteRepository,
)
from app.identity_handover.service import IdentityHandoverRouteService
from app.roi_connections.adapters.memory import InMemoryRoiConnectionRepository
from app.roi_connections.service import RoiConnectionService
from app.shared.adapters.memory_student_lookup import InMemoryStudentLookup
from app.video_monitoring.adapters.memory_repository import MemoryVideoStreamRepository
from app.video_monitoring.models import CameraRole, PlaybackKind, VideoStream

from .fakes import FakeCameraFrameGrabber

NOW = datetime(2026, 8, 23, 9, 0, tzinfo=UTC)


def stream(camera_id: str, role: CameraRole) -> VideoStream:
    return VideoStream(
        id=f"stream-{camera_id}",
        camera_id=camera_id,
        classroom_id="room",
        camera_label="입구 카메라" if role == CameraRole.IDENTITY_ONLY else "강의실 CCTV",
        playback_kind=PlaybackKind.WEBRTC,
        playback_path=f"/webrtc/{camera_id}",
        enabled=True,
        last_frame_at=None,
        last_detection_at=None,
        is_demo=False,
        created_at=NOW,
        updated_at=NOW,
        role=role,
    )


def make_service(
    *,
    grabber: FakeCameraFrameGrabber | None = None,
    streams: MemoryVideoStreamRepository | None = None,
) -> tuple[IdentityHandoverRouteService, MemoryVideoStreamRepository]:
    classrooms = ClassroomService(
        InMemoryClassroomRepository(),
        occupancy_confidence_threshold=0.5,
        clock=lambda: NOW,
    )
    classrooms.seed_classroom(
        CreateClassroomCommand(id="room", code="ROOM", name="테스트실", location="가상")
    )
    stream_repository = streams or MemoryVideoStreamRepository()
    stream_repository.save(stream("camera-01", CameraRole.IDENTITY_ONLY))
    stream_repository.save(stream("classroom-cctv", CameraRole.SEAT_JUDGING))
    camera = grabber or FakeCameraFrameGrabber()
    roi = RoiConnectionService(
        classrooms,
        InMemoryStudentLookup(()),
        InMemoryRoiConnectionRepository(),
        stream_repository,
        camera,
        max_upload_bytes=1024,
        page_size_max=20,
        clock=lambda: NOW,
    )
    return (
        IdentityHandoverRouteService(
            InMemoryIdentityHandoverRouteRepository(),
            roi,
            camera,
            max_image_bytes=1024,
            clock=lambda: NOW,
        ),
        stream_repository,
    )
