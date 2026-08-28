"""입구 얼굴 관측의 상태 갱신·실시간 표시 계약."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from app.entry_identity_events.adapters.memory import InMemoryEntryIdentityEventRepository
from app.entry_identity_events.errors import EntryIdentityCameraRoleError
from app.entry_identity_events.models import (
    EntryFaceObservation,
    EntryFrameInfo,
    EntryIdentityEventSaveResult,
    EntryIdentityProcessingStatus,
    EntryIdentityStatus,
)
from app.entry_identity_events.router import stream_entry_identity_events
from app.entry_identity_events.service import EntryIdentityEventService
from app.shared.broadcaster import InMemoryBroadcaster
from app.shared.config import Settings
from app.shared.student_identity import StudentIdentity, StudentIdentityPage
from app.video_monitoring.adapters.memory_repository import MemoryVideoStreamRepository
from app.video_monitoring.errors import VideoStreamNotFoundError
from app.video_monitoring.models import CameraRole, PlaybackKind, VideoStream

NOW = datetime(2026, 8, 25, 1, 0, tzinfo=UTC)


class FakeStudentLookup:
    def __init__(
        self,
        students: dict[str, StudentIdentity] | None = None,
        *,
        fail: bool = False,
    ) -> None:
        self._students = students or {}
        self._fail = fail

    def find_by_id(self, student_id: str) -> StudentIdentity | None:
        if self._fail:
            raise RuntimeError(f"조회 실패: {student_id}")
        return self._students.get(student_id)

    def list_active(self, *, limit: int, offset: int) -> StudentIdentityPage:
        students = [student for student in self._students.values() if student.is_active]
        return StudentIdentityPage(students[offset : offset + limit], len(students))


class RecordingStreamRepository(MemoryVideoStreamRepository):
    def __init__(self) -> None:
        super().__init__()
        self.detection_updates: list[tuple[str, datetime]] = []

    def update_last_detection(self, camera_id: str, captured_at: datetime) -> None:
        self.detection_updates.append((camera_id, captured_at))
        super().update_last_detection(camera_id, captured_at)


def make_stream(
    stream_id: str,
    camera_id: str,
    *,
    role: CameraRole = CameraRole.IDENTITY_ONLY,
    enabled: bool = True,
) -> VideoStream:
    return VideoStream(
        id=stream_id,
        camera_id=camera_id,
        classroom_id="classroom-1",
        camera_label="입구",
        playback_kind=PlaybackKind.WEBRTC,
        playback_path=f"/webrtc/{camera_id}",
        enabled=enabled,
        last_frame_at=None,
        last_detection_at=None,
        is_demo=False,
        created_at=NOW,
        updated_at=NOW,
        role=role,
    )


def make_service(
    *,
    lookup: FakeStudentLookup | None = None,
) -> tuple[EntryIdentityEventService, InMemoryBroadcaster, RecordingStreamRepository]:
    streams = RecordingStreamRepository()
    streams.save(make_stream("entry-stream", "entry-camera"))
    broadcaster = InMemoryBroadcaster()
    service = EntryIdentityEventService(
        InMemoryEntryIdentityEventRepository(clock=lambda: NOW),
        streams,
        broadcaster,
        lookup or FakeStudentLookup(),
        retention_days=7,
        page_size_max=200,
        clock=lambda: NOW,
    )
    return service, broadcaster, streams


def observation(
    track_id: str,
    status: EntryIdentityStatus,
    *,
    student_id: str | None = None,
) -> EntryFaceObservation:
    return EntryFaceObservation(
        face_track_id=track_id,
        face_bbox=(10, 20, 110, 160),
        detection_confidence=0.97,
        identity_status=status,
        student_id=student_id,
        similarity=0.91,
        margin=0.24,
        quality=0.88,
        observation_count=4,
        rejected_reason=None,
    )


def save_event(
    service: EntryIdentityEventService,
    *,
    sequence: int = 1,
    observations: tuple[EntryFaceObservation, ...] = (),
    processing_status: EntryIdentityProcessingStatus = EntryIdentityProcessingStatus.SUCCEEDED,
) -> EntryIdentityEventSaveResult:
    captured_at = NOW + timedelta(seconds=sequence)
    return service.save_event(
        event_id=(f"entry-camera-{int(captured_at.timestamp() * 1000)}-{sequence}-entry-face"),
        camera_id="entry-camera",
        captured_at=captured_at,
        sequence=sequence,
        frame=EntryFrameInfo(width_pixels=640, height_pixels=480),
        processing_status=processing_status,
        observations=observations,
    )


def test_신규_얼굴_이벤트는_이름을_보강하고_안전한_본문만_한번_발행한다() -> None:
    lookup = FakeStudentLookup(
        {
            "active-student": StudentIdentity(
                id="active-student",
                student_no="2026001",
                name="김로운",
                is_active=True,
            ),
            "inactive-student": StudentIdentity(
                id="inactive-student",
                student_no="2025001",
                name="비활성 학생",
                is_active=False,
            ),
        }
    )
    service, broadcaster, streams = make_service(lookup=lookup)
    queue = broadcaster.subscribe()
    observations = (
        observation("face-1", EntryIdentityStatus.REGISTERED, student_id="active-student"),
        observation("face-2", EntryIdentityStatus.REGISTERED, student_id="inactive-student"),
        observation("face-3", EntryIdentityStatus.REGISTERED, student_id="missing-student"),
        observation("face-4", EntryIdentityStatus.UNKNOWN),
        observation("face-5", EntryIdentityStatus.UNCERTAIN),
    )

    created = save_event(service, observations=observations)
    event = queue.get_nowait()
    duplicate = save_event(service, observations=observations)

    assert created.created is True
    assert duplicate.created is False
    assert queue.empty()
    assert streams.detection_updates == [
        ("entry-camera", NOW + timedelta(seconds=1)),
        ("entry-camera", NOW + timedelta(seconds=1)),
    ]
    restored = streams.find_by_camera_id("entry-camera")
    assert restored is not None
    assert restored.last_detection_at == NOW + timedelta(seconds=1)
    assert restored.role is CameraRole.IDENTITY_ONLY
    assert [item["display_label"] for item in event["observations"]] == [
        "김로운",
        "등록 얼굴",
        "등록 얼굴",
        "미등록 얼굴",
        "판정 보류",
    ]
    serialized = json.dumps(event, ensure_ascii=False)
    for forbidden in (
        "student_id",
        "student_no",
        "similarity",
        "margin",
        "quality",
        "observation_count",
        "rejected_reason",
        "2026001",
    ):
        assert forbidden not in serialized


def test_학생_조회_실패는_식별자를_로그에_남기지_않고_대체_라벨을_쓴다(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service, broadcaster, _ = make_service(lookup=FakeStudentLookup(fail=True))
    queue = broadcaster.subscribe()

    save_event(
        service,
        observations=(
            observation(
                "face-private",
                EntryIdentityStatus.REGISTERED,
                student_id="private-student-id",
            ),
        ),
    )

    event = queue.get_nowait()
    assert event["observations"][0]["display_label"] == "등록 얼굴"
    assert "private-student-id" not in caplog.text
    assert "face-private" not in caplog.text


def test_분석_실패_이벤트도_상태와_마지막_탐지_시각을_전달한다() -> None:
    service, broadcaster, streams = make_service()
    queue = broadcaster.subscribe()

    save_event(
        service,
        sequence=2,
        processing_status=EntryIdentityProcessingStatus.ANALYZER_UNAVAILABLE,
    )

    event = queue.get_nowait()
    assert event["processing_status"] == "ANALYZER_UNAVAILABLE"
    assert event["observations"] == []
    assert event["observations_count"] == 0
    restored = streams.find_by_camera_id("entry-camera")
    assert restored is not None
    assert restored.last_detection_at == NOW + timedelta(seconds=2)


def test_얼굴_SSE는_요청한_입구_카메라_이벤트만_전달한다() -> None:
    service, broadcaster, _ = make_service()

    async def scenario() -> None:
        response = await stream_entry_identity_events(
            "entry-stream",
            service=service,
            broadcaster=broadcaster,
            settings=Settings(
                sse_heartbeat_interval_seconds=1,
                sse_reconnection_timeout_seconds=2,
            ),
        )
        iterator = response.body_iterator.__aiter__()
        try:
            assert await anext(iterator) == "retry: 2000\n\n"
            broadcaster.publish(
                {
                    "type": "entry-identity",
                    "event_id": "other-event",
                    "camera_id": "other-camera",
                }
            )
            broadcaster.publish(
                {
                    "type": "detection",
                    "event_id": "object-event",
                    "camera_id": "entry-camera",
                }
            )
            saved = save_event(
                service,
                observations=(observation("face-1", EntryIdentityStatus.UNKNOWN),),
            )

            event_id_chunk = await anext(iterator)
            event_type_chunk = await anext(iterator)
            data_chunk = await anext(iterator)

            assert event_id_chunk == f"id: {saved.event.event_id}\n"
            assert event_type_chunk == "event: entry-identity\n"
            assert '"camera_id": "entry-camera"' in data_chunk
            assert "other-event" not in data_chunk
            assert "object-event" not in data_chunk
        finally:
            await cast(AsyncGenerator[str, None], iterator).aclose()

    asyncio.run(scenario())


def test_얼굴_SSE는_활성_입구_카메라에서만_열린다() -> None:
    service, _, streams = make_service()
    streams.save(
        make_stream(
            "cctv-stream",
            "classroom-cctv",
            role=CameraRole.SEAT_JUDGING,
        )
    )
    streams.save(make_stream("inactive-entry", "inactive-entry", enabled=False))

    assert service.resolve_realtime_camera_id("entry-stream") == "entry-camera"
    assert service.resolve_realtime_camera_id("entry-camera") == "entry-camera"
    with pytest.raises(EntryIdentityCameraRoleError):
        service.resolve_realtime_camera_id("cctv-stream")
    with pytest.raises(EntryIdentityCameraRoleError):
        service.resolve_realtime_camera_id("inactive-entry")
    with pytest.raises(VideoStreamNotFoundError):
        service.resolve_realtime_camera_id("missing-stream")


def test_오버레이는_저장하지_않고_발행만_한다() -> None:
    """화면에 얼굴 상자를 그리는 데 저장소가 필요 없다.

    저장을 끼우면 저장 주기가 곧 화면 갱신 주기가 된다 — CCTV에서 같은 문제를 겪고
    갈랐다(결정 0047).
    """
    service, broadcaster, streams = make_service()
    queue = broadcaster.subscribe()
    captured_at = NOW + timedelta(seconds=1)

    service.publish_overlay(
        event_id=f"entry-camera-{int(captured_at.timestamp() * 1000)}-1-entry-face",
        camera_id="entry-camera",
        captured_at=captured_at,
        sequence=1,
        frame=EntryFrameInfo(width_pixels=640, height_pixels=480),
        processing_status=EntryIdentityProcessingStatus.SUCCEEDED,
        observations=(observation("face-1", EntryIdentityStatus.UNKNOWN),),
    )

    event = queue.get_nowait()
    assert event["type"] == "entry-identity"
    assert event["camera_id"] == "entry-camera"
    assert event["observations"][0]["face_bbox"] == [10, 20, 110, 160]
    # 저장하지 않으므로 마지막 탐지 시각도 건드리지 않는다. save_event였다면 남는다.
    assert streams.detection_updates == []
    assert queue.empty()


def test_오버레이는_stream_확인_없이도_발행한다() -> None:
    """구독자가 camera_id로 걸러 받으므로 등록되지 않은 카메라는 아무에게도 닿지 않는다.

    확인하려면 저장소 왕복이 생겨 이 경로를 만든 이유가 없어진다.
    """
    service, broadcaster, streams = make_service()
    queue = broadcaster.subscribe()
    captured_at = NOW + timedelta(seconds=1)

    service.publish_overlay(
        event_id="아무-id",
        camera_id="등록되지-않은-카메라",
        captured_at=captured_at,
        sequence=1,
        frame=EntryFrameInfo(width_pixels=640, height_pixels=480),
        processing_status=EntryIdentityProcessingStatus.SUCCEEDED,
        observations=(observation("face-1", EntryIdentityStatus.UNKNOWN),),
    )

    # save_event였다면 VideoStreamNotFoundError로 막혔을 카메라다.
    assert queue.get_nowait()["camera_id"] == "등록되지-않은-카메라"
    assert streams.detection_updates == []
