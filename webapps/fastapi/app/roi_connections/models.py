"""ROI 연결 도메인 값."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class ReferenceImage:
    classroom_id: str
    content_type: str
    content: bytes
    display_name: str
    revision: int


@dataclass(frozen=True)
class RoiConnection:
    classroom_id: str
    seat_id: str
    student_id: str | None
    polygon: tuple[Point, ...]
    reference_image_revision: int
    updated_at: datetime


@dataclass(frozen=True)
class RoiConnectionView:
    connection: RoiConnection
    needs_review: bool


@dataclass(frozen=True)
class SaveRoiConnectionCommand:
    classroom_id: str
    seat_id: str
    student_id: str | None
    polygon: tuple[Point, ...]
    reference_image_revision: int


@dataclass(frozen=True)
class SaveLiveRoiConnectionCommand:
    classroom_id: str
    seat_id: str
    student_id: str
    polygon: tuple[Point, ...]
