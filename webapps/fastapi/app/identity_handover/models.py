"""신원 인계 route 도메인 값."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class HandoverZone:
    """CCTV 프레임의 0~1 정규화 사각형."""

    left: float
    top: float
    right: float
    bottom: float

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.left, self.top, self.right, self.bottom)


@dataclass(frozen=True)
class HandoverReferenceImage:
    classroom_id: str
    camera_id: str
    content: bytes
    display_name: str
    revision: int


@dataclass(frozen=True)
class IdentityHandoverRoute:
    classroom_id: str
    entry_camera_id: str
    classroom_camera_id: str
    classroom_entry_zone: HandoverZone
    reference_image_revision: int
    updated_at: datetime


@dataclass(frozen=True)
class SaveIdentityHandoverRouteCommand:
    classroom_id: str
    entry_camera_id: str
    classroom_camera_id: str
    classroom_entry_zone: HandoverZone
    reference_image_revision: int


@dataclass(frozen=True)
class HandoverCameraOption:
    camera_id: str
    camera_label: str
    capture_available: bool


@dataclass(frozen=True)
class HandoverPageOptions:
    entry_cameras: tuple[HandoverCameraOption, ...]
    classroom_cameras: tuple[HandoverCameraOption, ...]
