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
    camera_id: str
    content_type: str
    content: bytes
    display_name: str
    revision: int


@dataclass(frozen=True)
class RoiCameraOption:
    """ROI 화면의 카메라 선택 항목.

    "이 카메라의 현재 화면을 캡처할 수 있는가"는 접속 정보 설정 여부를 보고 정하는
    판단이므로 템플릿이 아니라 서비스가 계산해 넘긴다.
    """

    camera_id: str
    camera_label: str
    capture_available: bool


@dataclass(frozen=True)
class RoiConnection:
    classroom_id: str
    camera_id: str | None
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
    camera_id: str
    seat_id: str
    student_id: str | None
    polygon: tuple[Point, ...]
    reference_image_revision: int


@dataclass(frozen=True)
class SaveLiveRoiConnectionCommand:
    classroom_id: str
    camera_id: str
    seat_id: str
    student_id: str
    polygon: tuple[Point, ...]
