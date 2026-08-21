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
from .video_monitoring.service import VideoStreamService


def seed_demo_data(service: ClassroomService, *, now: datetime) -> None:
    if now.tzinfo is None:
        raise ValueError("demo seed 시각은 timezone-aware 값이어야 합니다.")
    for classroom_index, (code, name, location) in enumerate(
        (
            ("A101", "A101 일반 강의실", "A동 1층"),
            ("B203", "B203 실습실", "B동 2층"),
        )
    ):
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
                    row=((index - 1) // 3) + 1,
                    column=((index - 1) % 3) + 1,
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
                    # 6번 좌석은 "사람이 잡혔지만 임계값 미만" 표본이다. 화면에서
                    # UNKNOWN을 확인하려면 점유 근거가 있으면서 신뢰도가 낮아야 한다
                    # — 빈 관측은 이제 VACANT가 된다.
                    SeatObservation(
                        seat_id=seat.id,
                        occupied=index % 3 != 0 or index == 6,
                        confidence=0.35 if index == 6 else 0.95,
                    )
                    for index, seat in enumerate(seats, start=1)
                ),
            )
        )


def seed_roi_test_data(service: ClassroomService) -> None:
    """memory mode의 ROI 페이지에서만 사용할 가상 강의실과 좌석을 멱등하게 만든다."""
    classroom = service.seed_classroom(
        CreateClassroomCommand(
            id="roi-test-classroom",
            code="ROI-TEST",
            name="ROI 연결 테스트 강의실",
            location="가상 데이터",
        )
    )
    for index in range(1, 7):
        service.seed_seat(
            CreateSeatCommand(
                id=f"roi-test-seat-{index}",
                classroom_id=classroom.id,
                code=f"R{index:02d}",
                label=f"테스트 좌석 {index}",
                row=((index - 1) // 3) + 1,
                column=((index - 1) % 3) + 1,
            )
        )


def seed_video_streams(service: VideoStreamService, *, now: datetime) -> None:
    """실제 source를 등록한다. WebRTC 재생·탐지 수신 테스트용 fixture다."""
    if now.tzinfo is None:
        raise ValueError("demo seed 시각은 timezone-aware 값이어야 합니다.")
    for camera_id, classroom_code, label in (
        ("camera-01", "A101", "A101 전면 카메라"),
        ("camera-02", "B203", "B203 전면 카메라"),
    ):
        classroom_id = _entity_id(f"classroom-{classroom_code.lower()}")
        service.save_stream(
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
