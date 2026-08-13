"""local memory mode의 개인정보 없는 강의실·좌석 demo fixture."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from .classrooms.models import (
    CreateClassroomCommand,
    CreateSeatCommand,
    OccupancySource,
    RecordSeatObservationBatchCommand,
    SeatGeometry,
    SeatObservation,
)
from .classrooms.service import ClassroomService
from .video_monitoring.models import PlaybackKind, VideoStream
from .video_monitoring.ports import VideoStreamRepository


def seed_demo_data(service: ClassroomService, *, now: datetime) -> None:
    if now.tzinfo is None:
        raise ValueError("demo seed 시각은 timezone-aware 값이어야 합니다.")
    classrooms = (
        ("A101", "A101 일반 강의실", "A동 1층"),
        ("B203", "B203 실습실", "B동 2층"),
    )
    # base.html의 네비게이션(학생 상태·좌석-학생 지정)이 고정 ID로 연결하므로
    # 해당 ID의 강의실을 항상 시드한다. seed_classroom은 멱등이라 재실행해도 안전하다.
    # 좌석은 없어도 화면은 정상 렌더링된다.
    service.seed_classroom(
        CreateClassroomCommand(
            id="demo-classroom",
            code="DEMO",
            name="데모 강의실",
            location="데모동 1층",
        )
    )
    for classroom_index, (code, name, location) in enumerate(classrooms):
        classroom = service.seed_classroom(
            CreateClassroomCommand(
                id=_entity_id(f"classroom-{code.lower()}"),
                code=code,
                name=name,
                location=location,
            )
        )
        seats = [
            service.seed_seat(
                CreateSeatCommand(
                    id=_entity_id(f"seat-{code.lower()}-{index}"),
                    classroom_id=classroom.id,
                    code=f"S{index:02d}",
                    label=f"좌석 {index}",
                    geometry=SeatGeometry(
                        x=0.08 + ((index - 1) % 3) * 0.3,
                        y=0.16 + ((index - 1) // 3) * 0.36,
                        width=0.2,
                        height=0.24,
                    ),
                )
            )
            for index in range(1, 7)
        ]
        observed_at = now.astimezone(UTC) - timedelta(minutes=classroom_index + 1)
        service.record_observation_batch(
            RecordSeatObservationBatchCommand(
                event_id=_entity_id(f"seat-observation-{code.lower()}"),
                classroom_id=classroom.id,
                source=OccupancySource.MOCK,
                observed_at=observed_at,
                observations=tuple(
                    SeatObservation(
                        seat_id=seat.id,
                        occupied=index % 3 != 0,
                        confidence=0.35 if index == 6 else 0.95,
                    )
                    for index, seat in enumerate(seats, start=1)
                ),
            )
        )


def seed_video_streams(repository: VideoStreamRepository, *, now: datetime) -> None:
    """실제 source를 등록한다. WebRTC 재생·탐지 수신 테스트용 fixture다."""
    if now.tzinfo is None:
        raise ValueError("demo seed 시각은 timezone-aware 값이어야 합니다.")
    for camera_id, classroom_id, label in (
        ("camera-01", "classroom-a101", "A101 전면 카메라"),
        ("camera-02", "classroom-b203", "B203 전면 카메라"),
    ):
        existing = repository.find_by_camera_id(camera_id)
        if existing is not None:
            continue
        repository.save(
            VideoStream(
                id=_entity_id(f"stream-{camera_id}"),
                camera_id=camera_id,
                classroom_id=classroom_id,
                camera_label=label,
                playback_kind=PlaybackKind.WEBRTC,
                playback_path=f"/webrtc/{camera_id}",
                enabled=True,
                last_frame_at=None,
                last_detection_at=None,
                is_demo=False,
                created_at=now,
                updated_at=now,
            )
        )


def _entity_id(name: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"smart-office-minimal-demo:{name}"))