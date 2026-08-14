"""강의실 좌석 점유의 최소 도메인 값."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class SeatOccupancy(StrEnum):
    VACANT = "VACANT"
    OCCUPIED = "OCCUPIED"
    UNKNOWN = "UNKNOWN"


class OccupancySource(StrEnum):
    SYSTEM = "SYSTEM"
    MOCK = "MOCK"


class ObservationBatchStatus(StrEnum):
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"


@dataclass(frozen=True)
class Classroom:
    id: str
    code: str
    name: str
    location: str
    is_active: bool
    created_at: datetime


@dataclass(frozen=True)
class ClassroomPage:
    items: list[Classroom]
    total: int


@dataclass(frozen=True)
class SeatGeometry:
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class SeatCurrentOccupancy:
    state: SeatOccupancy
    source: OccupancySource
    confidence: float | None
    observed_at: datetime | None
    event_id: str | None


@dataclass(frozen=True)
class Seat:
    id: str
    classroom_id: str
    code: str
    label: str
    row: int | None = None  # 새로 추가 (1부터 시작, None 허용)
    column: int | None = None  # 새로 추가 (1부터 시작, None 허용)
    geometry: SeatGeometry | None = None
    is_active: bool = True
    current_occupancy: SeatCurrentOccupancy = field(
        default_factory=lambda: SeatCurrentOccupancy(
            state=SeatOccupancy.UNKNOWN,
            source=OccupancySource.SYSTEM,
            confidence=None,
            observed_at=None,
            event_id=None,
        )
    )
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    version: int = 0


@dataclass(frozen=True)
class SeatPage:
    items: list[Seat]
    total: int


@dataclass(frozen=True)
class SeatObservation:
    seat_id: str
    occupied: bool
    confidence: float


@dataclass(frozen=True)
class SeatObservationBatchRecord:
    event_id: str
    classroom_id: str
    source: OccupancySource
    observed_at: datetime
    observations: tuple[SeatObservation, ...]
    status: ObservationBatchStatus
    processed_count: int
    changed_count: int
    received_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True)
class SeatOccupancyHistory:
    id: str
    seat_id: str
    classroom_id: str
    event_id: str
    source: OccupancySource
    from_state: SeatOccupancy
    to_state: SeatOccupancy
    occupied: bool
    confidence: float
    observed_at: datetime
    received_at: datetime
    applied_to_current: bool
    state_changed: bool


@dataclass(frozen=True)
class SeatObservationBatchResult:
    event_id: str
    processed_count: int
    changed_count: int


@dataclass(frozen=True)
class ClassroomOccupancySummary:
    classroom: Classroom
    seats: list[Seat]
    total: int
    occupied_count: int
    vacant_count: int
    unknown_count: int
    last_observed_at: datetime | None


@dataclass(frozen=True)
class CreateClassroomCommand:
    id: str
    code: str
    name: str
    location: str


@dataclass(frozen=True)
class CreateSeatCommand:
    id: str
    classroom_id: str
    code: str
    label: str
    row: int | None = None  # 새로 추가 (None 허용)
    column: int | None = None  # 새로 추가 (None 허용)
    geometry: SeatGeometry | None = None


@dataclass(frozen=True)
class RecordSeatObservationBatchCommand:
    event_id: str
    classroom_id: str
    source: OccupancySource
    observed_at: datetime
    observations: tuple[SeatObservation, ...]


# ============================================================
# 좌석-학생 지정 모델
# ============================================================


@dataclass(frozen=True)
class SeatAssignment:
    """좌석-학생 지정."""

    seat_id: str
    student_id: str
    classroom_id: str
    assigned_at: datetime


@dataclass(frozen=True)
class SeatAssignmentInfo:
    """좌석-학생 지정 상세 (조회용)."""

    seat_id: str
    seat_label: str
    student_id: str
    student_name: str
    student_no: str
    assigned_at: datetime


@dataclass(frozen=True)
class AssignStudentCommand:
    """학생 지정 명령."""

    seat_id: str
    student_id: str
    classroom_id: str


@dataclass(frozen=True)
class UnassignStudentCommand:
    """학생 지정 해제 명령."""

    seat_id: str


# ============================================================
# 오프라인 migration 모델
# ============================================================


class SeatMigrationAction(StrEnum):
    """개별 좌석 migration 기록의 종류."""

    APPEND = "APPEND"  # legacy null 쌍에 좌표를 append
    REPAIR = "REPAIR"  # 수동 repair (승인·감사)
    RESTORE = "RESTORE"  # snapshot 복원


class RepairApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class SeatMigrationSnapshot:
    """오프라인 migration backup manifest.

    비민감 manifest(id/name/count/checksum/created/expiry)만 담는다.
    좌석·지정 내용, seat/student ID, archive path, encryption material은
    담지 않는다 — 실제 데이터는 payload로 저장소에 보관하고 manifest에는
    seats·assignments 쌍의 checksum만 남긴다.
    """

    id: str
    classroom_id: str
    name: str
    seats_count: int
    assignments_count: int
    checksum: str
    created_at: datetime
    expires_at: datetime
    restored_at: datetime | None = None


@dataclass(frozen=True)
class SeatMigrationSnapshotPayload:
    """snapshot에 보관되는 실제 좌석·지정 데이터 (복원용)."""

    seats: tuple[Seat, ...]
    assignments: tuple[SeatAssignment, ...]


@dataclass(frozen=True)
class SeatMigrationRecord:
    """좌석별 migration 감사 기록.

    seat ID별 감사·추적을 위해 seat_id를 항상 담는다.
    APPEND·RESTORE는 snapshot_id를, REPAIR는 snapshot_id 없이 기록된다.
    """

    id: str
    classroom_id: str
    seat_id: str
    action: SeatMigrationAction
    previous_row: int | None
    previous_column: int | None
    new_row: int | None
    new_column: int | None
    created_at: datetime
    snapshot_id: str | None = None


@dataclass(frozen=True)
class RepairApproval:
    """수동 repair 승인 기록.

    seat ID별 감사와 승인을 남긴다. 승인된 미사용 양수 좌표 또는
    양쪽 unset(둘 다 None)만 허용한다.
    """

    id: str
    classroom_id: str
    seat_id: str
    requested_row: int | None
    requested_column: int | None
    requested_by: str
    requested_at: datetime
    status: RepairApprovalStatus
    approved_by: str | None = None
    approved_at: datetime | None = None
