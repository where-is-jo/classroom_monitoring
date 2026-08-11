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


def _entity_id(name: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"smart-office-minimal-demo:{name}"))
