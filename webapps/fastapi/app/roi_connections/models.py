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
    auto_generated: bool = False
    """사람이 그리지 않고 계산으로 만든 좌표이며, 관리자가 아직 확정하지 않았다.

    좌석 격자를 사영한 것(결정 0039)이든 탐지 밀도에서 찾은 것(결정 0041)이든 같다.
    계산으로 만든 좌표는 근거가 실제와 어긋나 있으면 조용히 틀린 좌석을 가리킨다.
    확정 전까지 `needs_review`로 두어 좌석 판정에서 빼는 이유다
    ([결정 0020](../../../docs/architecture/decisions.md)의 6번).
    """


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


@dataclass(frozen=True)
class ConfirmAutoRoiCommand:
    """자동 생성분을 좌석 판정에 쓰겠다고 확정한다.

    `seat_ids`가 `None`이면 그 카메라의 자동 생성분 전체를 확정한다.
    """

    classroom_id: str
    camera_id: str
    seat_ids: tuple[str, ...] | None


@dataclass(frozen=True)
class ConfirmAutoRoiResult:
    """확정 결과. 기준 화면이 바뀐 것은 확정하지 않고 남겨 둔다."""

    confirmed_count: int
    stale_count: int


@dataclass(frozen=True)
class PlanDetectionRoiCommand:
    """탐지가 몰린 자리를 찾아 달라는 요청. 저장하지 않는다."""

    classroom_id: str
    camera_id: str
    lookback_hours: int


@dataclass(frozen=True)
class DetectionRoiProposal:
    """탐지에서 찾은 자리 하나. 어느 좌석인지는 아직 정해지지 않았다."""

    index: int
    polygon: tuple[Point, ...]
    sample_count: int
    suggested_seat_id: str | None
    """이 자리에 이미 ROI가 있는 좌석. 다시 만들 때 좌석을 새로 고르지 않게 하는 힌트다."""


@dataclass(frozen=True)
class DetectionRoiPlanResult:
    classroom_id: str
    camera_id: str
    window_from: datetime
    window_to: datetime
    sample_count: int
    stationary_count: int
    dropped_overlapping: int
    dropped_weak: int
    proposals: tuple[DetectionRoiProposal, ...]


@dataclass(frozen=True)
class DetectionRoiAssignment:
    seat_id: str
    polygon: tuple[Point, ...]


@dataclass(frozen=True)
class ApplyDetectionRoiCommand:
    """관리자가 좌석을 지정한 자리들을 저장한다."""

    classroom_id: str
    camera_id: str
    assignments: tuple[DetectionRoiAssignment, ...]


@dataclass(frozen=True)
class ApplyDetectionRoiResult:
    saved_count: int
    seat_ids: tuple[str, ...]
