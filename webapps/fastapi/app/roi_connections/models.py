"""ROI 연결 도메인 값."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


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
    """좌석 격자를 사영해 만든 좌표이며 관리자가 아직 확정하지 않았다.

    계산으로 만든 좌표는 격자와 실제 배치가 어긋나 있으면 조용히 틀린 좌석을 가리킨다.
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
class GenerateAutoRoiCommand:
    """좌석 구역 네 모서리에서 좌석 ROI를 만들어 달라는 요청.

    `dry_run`이면 계산만 하고 저장하지 않는다. 관리자가 화면에서 먼저 확인하게 하려는
    것이다 — 격자가 실제 배치와 어긋났는지는 겹쳐 보기 전에는 알 수 없다.
    """

    classroom_id: str
    camera_id: str
    corners: tuple[Point, ...]
    reference_image_revision: int
    seat_fill_ratio: float
    dry_run: bool


@dataclass(frozen=True)
class ConfirmAutoRoiCommand:
    """자동 생성분을 좌석 판정에 쓰겠다고 확정한다.

    `seat_ids`가 `None`이면 그 카메라의 자동 생성분 전체를 확정한다.
    """

    classroom_id: str
    camera_id: str
    seat_ids: tuple[str, ...] | None


class AutoRoiOutcome(StrEnum):
    """좌석 하나에 대한 자동 생성 결과."""

    GENERATED = "GENERATED"
    """격자 좌표를 사영해 ROI를 만들었다."""

    EXISTING_KEPT = "EXISTING_KEPT"
    """이미 ROI가 있어 건드리지 않았다."""

    NO_GRID_POSITION = "NO_GRID_POSITION"
    """좌석에 행·열 좌표가 없어 격자 위 자리를 알 수 없다."""

    TOO_SMALL = "TOO_SMALL"
    """사영한 넓이가 너무 작아 관리자가 확인할 수 없다."""

    INVALID_POLYGON = "INVALID_POLYGON"
    """만든 좌표가 ROI 규칙을 통과하지 못했다. 저장하지 않는다."""


@dataclass(frozen=True)
class AutoRoiSeatResult:
    """좌석 하나의 자동 생성 결과. 건너뛴 좌석도 이유와 함께 남긴다."""

    seat_id: str
    seat_label: str
    outcome: AutoRoiOutcome
    polygon: tuple[Point, ...] | None


@dataclass(frozen=True)
class AutoRoiResult:
    classroom_id: str
    camera_id: str
    dry_run: bool
    grid_rows: int
    grid_columns: int
    seat_fill_ratio: float
    reference_image_revision: int
    seats: tuple[AutoRoiSeatResult, ...]

    @property
    def generated_count(self) -> int:
        return sum(1 for seat in self.seats if seat.outcome is AutoRoiOutcome.GENERATED)

    @property
    def skipped_count(self) -> int:
        return len(self.seats) - self.generated_count


@dataclass(frozen=True)
class ConfirmAutoRoiResult:
    """확정 결과. 기준 화면이 바뀐 것은 확정하지 않고 남겨 둔다."""

    confirmed_count: int
    stale_count: int
