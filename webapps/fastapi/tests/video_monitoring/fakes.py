"""video_monitoring 테스트 공용 대역 (시계, WHEP client, stream fixture)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.classrooms.adapters.memory_repository import InMemoryClassroomRepository
from app.classrooms.models import CreateClassroomCommand
from app.classrooms.service import ClassroomService
from app.video_monitoring.models import PlaybackKind, VideoStream
from app.video_monitoring.ports import WhepPostResult

NOW = datetime(2026, 8, 14, 3, 0, tzinfo=UTC)
WHEP_BASE_URL = "http://127.0.0.1:8889"
ANSWER_SDP = "v=0\r\nanswer"


class FakeClock:
    """제어 가능한 시계."""

    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FakeWhepClient:
    """WHEP client 대역. 호출 기록과 오류 주입을 지원한다."""

    def __init__(self) -> None:
        self.posted: list[tuple[str, str]] = []
        self.patched: list[tuple[str, str]] = []
        self.deleted: list[str] = []
        self.post_result = WhepPostResult(
            answer_sdp=ANSWER_SDP, resource_location="/webrtc/camera-01/whep"
        )
        self.patch_result = "v=0\r\nanswer-patch"
        self.post_error: Exception | None = None
        self.patch_error: Exception | None = None
        self.delete_error: Exception | None = None

    def post_offer(self, target_url: str, sdp: str) -> WhepPostResult:
        self.posted.append((target_url, sdp))
        if self.post_error is not None:
            raise self.post_error
        return self.post_result

    def patch_offer(self, resource_url: str, sdp: str) -> str:
        self.patched.append((resource_url, sdp))
        if self.patch_error is not None:
            raise self.patch_error
        return self.patch_result

    def delete(self, resource_url: str) -> None:
        self.deleted.append(resource_url)
        if self.delete_error is not None:
            raise self.delete_error


def make_classroom_service(*, active: bool = True) -> ClassroomService:
    service = ClassroomService(
        InMemoryClassroomRepository(),
        occupancy_confidence_threshold=0.5,
        clock=FakeClock(),
    )
    service.seed_classroom(
        CreateClassroomCommand(
            id="classroom-a101",
            code="A101",
            name="A101 일반 강의실",
            location="A동 1층",
        )
    )
    if not active:
        service.update_classroom("classroom-a101", is_active=False)
    return service


def make_stream(
    *,
    stream_id: str = "stream-01",
    camera_id: str = "camera-01",
    enabled: bool = True,
    is_demo: bool = False,
    playback_kind: PlaybackKind = PlaybackKind.WEBRTC,
) -> VideoStream:
    return VideoStream(
        id=stream_id,
        camera_id=camera_id,
        classroom_id="classroom-a101",
        camera_label="A101 전면 카메라",
        playback_kind=playback_kind,
        playback_path=f"/webrtc/{camera_id}",
        enabled=enabled,
        last_frame_at=None,
        last_detection_at=None,
        is_demo=is_demo,
        created_at=NOW,
        updated_at=NOW,
    )
