"""강의실 좌석 조회 HTTP schema."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .migration import (
    SeatMigrationPreflight,
    SeatMigrationResult,
    SeatMigrationStatus,
    SeatMigrationValidation,
)
from .models import (
    Classroom,
    ClassroomOccupancySummary,
    ClassroomPage,
    RepairApproval,
    Seat,
    SeatAssignmentInfo,
    SeatGeometry,
    SeatMigrationRecord,
    SeatMigrationSnapshot,
    SeatPage,
)


class ClassroomCreateRequest(BaseModel):
    """강의실 생성 요청."""

    code: str
    name: str
    location: str


class ClassroomUpdateRequest(BaseModel):
    """강의실 수정 요청. 전달한 필드만 갱신한다."""

    code: str | None = None
    name: str | None = None
    location: str | None = None
    is_active: bool | None = None


class ClassroomResponse(BaseModel):
    id: str
    code: str
    name: str
    location: str
    is_active: bool
    created_at: datetime

    @classmethod
    def from_domain(cls, item: Classroom) -> ClassroomResponse:
        return cls(
            id=item.id,
            code=item.code,
            name=item.name,
            location=item.location,
            is_active=item.is_active,
            created_at=item.created_at,
        )


class ClassroomListResponse(BaseModel):
    items: list[ClassroomResponse]
    total: int
    limit: int
    offset: int

    @classmethod
    def from_page(cls, page: ClassroomPage, limit: int, offset: int) -> ClassroomListResponse:
        return cls(
            items=[ClassroomResponse.from_domain(item) for item in page.items],
            total=page.total,
            limit=limit,
            offset=offset,
        )


class GeometryResponse(BaseModel):
    x: float
    y: float
    width: float
    height: float

    @classmethod
    def from_domain(cls, item: SeatGeometry) -> GeometryResponse:
        return cls(x=item.x, y=item.y, width=item.width, height=item.height)


class GeometryRequest(BaseModel):
    """좌석 geometry 입력. 0~1 정규화 좌표다."""

    x: float
    y: float
    width: float
    height: float

    def to_domain(self) -> SeatGeometry:
        return SeatGeometry(x=self.x, y=self.y, width=self.width, height=self.height)


class SeatCreateRequest(BaseModel):
    """좌석 생성 요청."""

    code: str
    label: str
    row: int | None = None
    column: int | None = None
    geometry: GeometryRequest | None = None


class AutoSeatCreateRequest(BaseModel):
    """좌석 자동 생성 요청.

    UI는 이 스키마만 사용하며 코드는 서버가 할당한다. row/column은 1 이상
    정수여야 하고, 정의되지 않은 추가 필드는 차단한다 (extra forbid).
    """

    model_config = ConfigDict(extra="forbid")

    row: int = Field(ge=1)
    column: int = Field(ge=1)


class SeatUpdateRequest(BaseModel):
    """좌석 수정 요청. 전달한 필드만 갱신한다."""

    code: str | None = None
    label: str | None = None
    row: int | None = None
    column: int | None = None
    geometry: GeometryRequest | None = None
    is_active: bool | None = None


class CurrentOccupancyResponse(BaseModel):
    state: str
    source: str
    confidence: float | None
    observed_at: datetime | None
    event_id: str | None


class SeatResponse(BaseModel):
    id: str
    classroom_id: str
    code: str
    label: str
    row: int | None = None
    column: int | None = None
    geometry: GeometryResponse | None = None
    is_active: bool
    current_occupancy: CurrentOccupancyResponse
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, item: Seat) -> SeatResponse:
        return cls(
            id=item.id,
            classroom_id=item.classroom_id,
            code=item.code,
            label=item.label,
            row=item.row,
            column=item.column,
            geometry=(
                None if item.geometry is None else GeometryResponse.from_domain(item.geometry)
            ),
            is_active=item.is_active,
            current_occupancy=CurrentOccupancyResponse(
                state=item.current_occupancy.state.value,
                source=item.current_occupancy.source.value,
                confidence=item.current_occupancy.confidence,
                observed_at=item.current_occupancy.observed_at,
                event_id=item.current_occupancy.event_id,
            ),
            created_at=item.created_at,
            updated_at=item.updated_at,
        )


class SeatListResponse(BaseModel):
    items: list[SeatResponse]
    total: int
    limit: int
    offset: int

    @classmethod
    def from_page(cls, page: SeatPage, limit: int, offset: int) -> SeatListResponse:
        return cls(
            items=[SeatResponse.from_domain(item) for item in page.items],
            total=page.total,
            limit=limit,
            offset=offset,
        )


class OccupancySummaryResponse(BaseModel):
    classroom: ClassroomResponse
    seats: list[SeatResponse]
    total: int
    occupied_count: int
    vacant_count: int
    unknown_count: int
    last_observed_at: datetime | None

    @classmethod
    def from_domain(cls, value: ClassroomOccupancySummary) -> OccupancySummaryResponse:
        return cls(
            classroom=ClassroomResponse.from_domain(value.classroom),
            seats=[SeatResponse.from_domain(item) for item in value.seats],
            total=value.total,
            occupied_count=value.occupied_count,
            vacant_count=value.vacant_count,
            unknown_count=value.unknown_count,
            last_observed_at=value.last_observed_at,
        )


# ============================================================
# 좌석-학생 지정 스키마
# ============================================================


class SeatAssignmentRequest(BaseModel):
    """좌석-학생 지정 요청."""

    student_id: str


class SeatAssignmentResponse(BaseModel):
    """좌석-학생 지정 응답."""

    seat_id: str
    student_id: str
    student_name: str
    assigned_at: datetime

    @classmethod
    def from_domain(cls, info: SeatAssignmentInfo) -> SeatAssignmentResponse:
        return cls(
            seat_id=info.seat_id,
            student_id=info.student_id,
            student_name=info.student_name,
            assigned_at=info.assigned_at,
        )


class SeatAssignmentListResponse(BaseModel):
    """좌석-학생 지정 목록 응답."""

    items: list[SeatAssignmentResponse]


# ============================================================
# 오프라인 migration 스키마
# ============================================================


class MigrationPreflightResponse(BaseModel):
    """preflight 검사 결과."""

    classroom_id: str
    ok: bool
    total_seats: int
    completed_count: int
    active_null_count: int
    inactive_null_count: int
    partial_coordinates_count: int
    blocked_reason: str | None = None

    @classmethod
    def from_domain(cls, value: SeatMigrationPreflight) -> MigrationPreflightResponse:
        return cls(
            classroom_id=value.classroom_id,
            ok=value.ok,
            total_seats=value.total_seats,
            completed_count=value.completed_count,
            active_null_count=value.active_null_count,
            inactive_null_count=value.inactive_null_count,
            partial_coordinates_count=value.partial_coordinates_count,
            blocked_reason=value.blocked_reason,
        )


class MigrationSnapshotResponse(BaseModel):
    """snapshot manifest (비민감 metadata만)."""

    id: str
    classroom_id: str
    name: str
    seats_count: int
    assignments_count: int
    checksum: str
    created_at: datetime
    expires_at: datetime
    restored_at: datetime | None = None

    @classmethod
    def from_domain(cls, value: SeatMigrationSnapshot) -> MigrationSnapshotResponse:
        return cls(
            id=value.id,
            classroom_id=value.classroom_id,
            name=value.name,
            seats_count=value.seats_count,
            assignments_count=value.assignments_count,
            checksum=value.checksum,
            created_at=value.created_at,
            expires_at=value.expires_at,
            restored_at=value.restored_at,
        )


class MigrationRecordResponse(BaseModel):
    """좌석별 migration 감사 기록."""

    id: str
    snapshot_id: str | None
    classroom_id: str
    seat_id: str
    action: str
    previous_row: int | None
    previous_column: int | None
    new_row: int | None
    new_column: int | None
    created_at: datetime

    @classmethod
    def from_domain(cls, value: SeatMigrationRecord) -> MigrationRecordResponse:
        return cls(
            id=value.id,
            snapshot_id=value.snapshot_id,
            classroom_id=value.classroom_id,
            seat_id=value.seat_id,
            action=value.action.value,
            previous_row=value.previous_row,
            previous_column=value.previous_column,
            new_row=value.new_row,
            new_column=value.new_column,
            created_at=value.created_at,
        )


class MigrationValidationResponse(BaseModel):
    """migration gate 검증 결과."""

    classroom_id: str
    ok: bool
    issues: list[str]
    seats_count: int
    assignments_count: int

    @classmethod
    def from_domain(cls, value: SeatMigrationValidation) -> MigrationValidationResponse:
        return cls(
            classroom_id=value.classroom_id,
            ok=value.ok,
            issues=list(value.issues),
            seats_count=value.seats_count,
            assignments_count=value.assignments_count,
        )


class MigrationRunRequest(BaseModel):
    """migration run 요청. snapshot 이름을 선택적으로 지정할 수 있다."""

    name: str | None = None


class MigrationRunResponse(BaseModel):
    """migration run 결과."""

    classroom_id: str
    snapshot: MigrationSnapshotResponse
    migrated_count: int
    skipped_with_coordinates_count: int
    inactive_null_skipped_count: int
    max_row: int
    records: list[MigrationRecordResponse]

    @classmethod
    def from_domain(cls, value: SeatMigrationResult) -> MigrationRunResponse:
        return cls(
            classroom_id=value.classroom_id,
            snapshot=MigrationSnapshotResponse.from_domain(value.snapshot),
            migrated_count=value.migrated_count,
            skipped_with_coordinates_count=value.skipped_with_coordinates_count,
            inactive_null_skipped_count=value.inactive_null_skipped_count,
            max_row=value.max_row,
            records=[MigrationRecordResponse.from_domain(record) for record in value.records],
        )


class MigrationRollbackRequest(BaseModel):
    """rollback 요청. snapshot_id를 선택적으로 지정할 수 있다."""

    snapshot_id: str | None = None


class MigrationRollbackResponse(BaseModel):
    """rollback 결과."""

    classroom_id: str
    snapshot: MigrationSnapshotResponse

    @classmethod
    def from_domain(
        cls, value: SeatMigrationSnapshot, classroom_id: str
    ) -> MigrationRollbackResponse:
        return cls(
            classroom_id=classroom_id,
            snapshot=MigrationSnapshotResponse.from_domain(value),
        )


class MigrationStatusResponse(BaseModel):
    """migration 상태 조회 결과."""

    classroom_id: str
    preflight: MigrationPreflightResponse
    snapshot: MigrationSnapshotResponse | None
    records: list[MigrationRecordResponse]
    validation: MigrationValidationResponse

    @classmethod
    def from_domain(cls, value: SeatMigrationStatus) -> MigrationStatusResponse:
        return cls(
            classroom_id=value.classroom_id,
            preflight=MigrationPreflightResponse.from_domain(value.preflight),
            snapshot=(
                None
                if value.snapshot is None
                else MigrationSnapshotResponse.from_domain(value.snapshot)
            ),
            records=[MigrationRecordResponse.from_domain(record) for record in value.records],
            validation=MigrationValidationResponse.from_domain(value.validation),
        )


class RepairRequestRequest(BaseModel):
    """수동 repair 승인 요청.

    좌표는 양쪽 모두 양수 정수 또는 양쪽 모두 unset(None)만 허용한다.
    부분 입력(행만·열만)·비양수는 서비스 계층에서 ``SeatRepairInvalidError``로 거부된다.
    """

    seat_id: str
    row: int | None = None
    column: int | None = None
    requested_by: str


class RepairApproveRequest(BaseModel):
    """수동 repair 승인 요청의 승인."""

    approval_id: str
    approved_by: str


class RepairExecuteRequest(BaseModel):
    """승인된 수동 repair 실행."""

    seat_id: str
    row: int | None = None
    column: int | None = None
    approved_by: str


class RepairApprovalResponse(BaseModel):
    """수동 repair 승인 기록 응답."""

    id: str
    classroom_id: str
    seat_id: str
    requested_row: int | None
    requested_column: int | None
    requested_by: str
    requested_at: datetime
    status: str
    approved_by: str | None = None
    approved_at: datetime | None = None

    @classmethod
    def from_domain(cls, value: RepairApproval) -> RepairApprovalResponse:
        return cls(
            id=value.id,
            classroom_id=value.classroom_id,
            seat_id=value.seat_id,
            requested_row=value.requested_row,
            requested_column=value.requested_column,
            requested_by=value.requested_by,
            requested_at=value.requested_at,
            status=value.status.value,
            approved_by=value.approved_by,
            approved_at=value.approved_at,
        )
