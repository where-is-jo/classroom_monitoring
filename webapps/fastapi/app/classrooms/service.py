"""Framework-independent classroom and seat policy service."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from math import isfinite
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..audit.service import AuditService
from ..auth.errors import PermissionDeniedError
from ..notifications.models import CreateNotificationCommand
from ..notifications.service import NotificationService
from ..users.models import ADMIN_ROLES, User, UserRole, UserStatus
from ..users.ports import UserRepository
from .errors import (
    AfterHoursAlertNotFoundError,
    AfterHoursAlertTransitionError,
    ClassroomConcurrentUpdateError,
    ClassroomInputError,
    ClassroomNotFoundError,
    ClassroomOperationConflictError,
    SeatBatchConflictError,
    SeatNotFoundError,
)
from .models import (
    AfterHoursAlert,
    AfterHoursAlertPage,
    AfterHoursAlertStatus,
    Classroom,
    ClassroomOccupancySummary,
    ClassroomPage,
    ClassroomSchedule,
    CreateClassroomCommand,
    CreateSeatCommand,
    ObservationBatchStatus,
    OccupancySource,
    RecordSeatObservationBatchCommand,
    ReplaceSchedulesCommand,
    ResolveAfterHoursAlertCommand,
    Seat,
    SeatCurrentOccupancy,
    SeatGeometry,
    SeatObservation,
    SeatObservationBatchRecord,
    SeatObservationBatchResult,
    SeatOccupancy,
    SeatOccupancyHistory,
    SeatOccupancyHistoryPage,
    SeatPage,
    UpdateClassroomCommand,
    UpdateSeatCommand,
)
from .ports import ClassroomRepository


class ClassroomStaffAssignmentService:
    def __init__(
        self,
        repository: ClassroomRepository,
        audit_service: AuditService,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._audit = audit_service
        self._clock = clock

    def unlink_staff_user(
        self,
        actor: User,
        user_id: str,
        *,
        operation_id: str,
        ip_fingerprint: str | None,
    ) -> None:
        classrooms: list[Classroom] = []
        offset = 0
        while True:
            page = self._repository.list_classrooms(
                include_inactive=True, limit=200, offset=offset
            )
            classrooms.extend(page.items)
            offset += len(page.items)
            if offset >= page.total or not page.items:
                break
        for classroom in classrooms:
            if user_id not in classroom.responsible_staff_user_ids:
                continue
            assignment_operation_id = (
                f"responsible-staff-unlink:{operation_id}:{classroom.id}"
            )
            for _ in range(3):
                current = self._repository.get_classroom(classroom.id)
                if current is None or user_id not in current.responsible_staff_user_ids:
                    break
                updated = replace(
                    current,
                    responsible_staff_user_ids=tuple(
                        value
                        for value in current.responsible_staff_user_ids
                        if value != user_id
                    ),
                    updated_at=self._clock(),
                    version=current.version + 1,
                    last_operation_id=assignment_operation_id,
                    operation_ids=ClassroomService._append_operation(
                        current.operation_ids, assignment_operation_id
                    ),
                )
                saved = self._repository.replace_classroom(
                    updated, expected_version=current.version
                )
                if saved is None:
                    continue
                self._audit.record(
                    operation_id=f"classroom-audit:{assignment_operation_id}",
                    actor_user_id=actor.id,
                    action="CLASSROOM_RESPONSIBLE_STAFF_UNLINKED",
                    resource_type="classroom",
                    resource_id=saved.id,
                    before={
                        "responsible_staff_user_ids": list(
                            current.responsible_staff_user_ids
                        )
                    },
                    after={
                        "responsible_staff_user_ids": list(
                            saved.responsible_staff_user_ids
                        )
                    },
                    ip_fingerprint=ip_fingerprint,
                )
                break
            else:
                raise ClassroomConcurrentUpdateError()


class ClassroomService:
    def __init__(
        self,
        repository: ClassroomRepository,
        user_repository: UserRepository,
        notification_service: NotificationService,
        audit_service: AuditService,
        *,
        occupancy_confidence_threshold: float,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._users = user_repository
        self._notifications = notification_service
        self._audit = audit_service
        self._threshold = occupancy_confidence_threshold
        self._clock = clock

    def create_classroom(
        self,
        actor: User,
        command: CreateClassroomCommand,
        *,
        ip_fingerprint: str | None,
    ) -> Classroom:
        self._require_admin(actor)
        operation_id = self._operation_id(command.operation_id)
        code = self._code(command.code)
        name = self._text(command.name, "강의실 이름")
        location = self._text(command.location, "강의실 위치")
        timezone = self._timezone(command.timezone)
        grace = self._grace(command.after_hours_grace_minutes)
        responsible_staff_user_ids = self._responsible_staff_user_ids(
            command.responsible_staff_user_ids
        )
        existing = self._repository.get_classroom_by_operation_id(operation_id)
        if existing is not None:
            if (
                existing.code != code
                or existing.name != name
                or existing.location != location
                or existing.timezone != timezone
                or existing.after_hours_grace_minutes != grace
                or existing.responsible_staff_user_ids != responsible_staff_user_ids
            ):
                raise ClassroomOperationConflictError()
            return existing
        now = self._clock()
        classroom = Classroom(
            id=str(uuid4()),
            code=code,
            name=name,
            location=location,
            timezone=timezone,
            schedules=(),
            after_hours_grace_minutes=grace,
            is_active=True,
            created_at=now,
            updated_at=now,
            version=0,
            created_operation_id=operation_id,
            last_operation_id=operation_id,
            operation_ids=(operation_id,),
            responsible_staff_user_ids=responsible_staff_user_ids,
        )
        saved = self._repository.create_classroom(classroom)
        self._audit_change(
            operation_id=operation_id,
            actor=actor,
            action="CLASSROOM_CREATED",
            resource_type="classroom",
            resource_id=saved.id,
            before=None,
            after=self._classroom_audit(saved),
            ip_fingerprint=ip_fingerprint,
        )
        return saved

    def list_classrooms(
        self,
        actor: User,
        *,
        include_inactive: bool,
        limit: int,
        offset: int,
    ) -> ClassroomPage:
        self._require_active(actor)
        if include_inactive and actor.role not in ADMIN_ROLES:
            include_inactive = False
        return self._repository.list_classrooms(
            include_inactive=include_inactive, limit=limit, offset=offset
        )

    def get_classroom(self, actor: User, classroom_id: str) -> Classroom:
        self._require_active(actor)
        classroom = self._required_classroom(classroom_id)
        if not classroom.is_active and actor.role not in ADMIN_ROLES:
            raise ClassroomNotFoundError()
        return classroom

    def update_classroom(
        self,
        actor: User,
        command: UpdateClassroomCommand,
        *,
        ip_fingerprint: str | None,
    ) -> Classroom:
        self._require_admin(actor)
        operation_id = self._operation_id(command.operation_id)
        code = self._code(command.code)
        name = self._text(command.name, "강의실 이름")
        location = self._text(command.location, "강의실 위치")
        timezone = self._timezone(command.timezone)
        grace = self._grace(command.after_hours_grace_minutes)
        responsible_staff_user_ids = self._responsible_staff_user_ids(
            command.responsible_staff_user_ids
        )
        existing = self._repository.get_classroom_by_operation_id(operation_id)
        if existing is not None:
            if (
                existing.id != command.classroom_id
                or existing.code != code
                or existing.name != name
                or existing.location != location
                or existing.timezone != timezone
                or existing.after_hours_grace_minutes != grace
                or existing.responsible_staff_user_ids != responsible_staff_user_ids
            ):
                raise ClassroomOperationConflictError()
            return existing
        current = self._required_classroom(command.classroom_id)
        updated = replace(
            current,
            code=code,
            name=name,
            location=location,
            timezone=timezone,
            after_hours_grace_minutes=grace,
            responsible_staff_user_ids=responsible_staff_user_ids,
            updated_at=self._clock(),
            version=current.version + 1,
            last_operation_id=operation_id,
            operation_ids=self._append_operation(current.operation_ids, operation_id),
        )
        saved = self._repository.replace_classroom(
            updated, expected_version=command.expected_version
        )
        if saved is None:
            raise ClassroomConcurrentUpdateError()
        self._audit_change(
            operation_id=operation_id,
            actor=actor,
            action="CLASSROOM_UPDATED",
            resource_type="classroom",
            resource_id=saved.id,
            before=self._classroom_audit(current),
            after=self._classroom_audit(saved),
            ip_fingerprint=ip_fingerprint,
        )
        return saved

    def deactivate_classroom(
        self,
        actor: User,
        classroom_id: str,
        *,
        expected_version: int,
        operation_id: str,
        ip_fingerprint: str | None,
    ) -> Classroom:
        self._require_admin(actor)
        operation_id = self._operation_id(operation_id)
        existing = self._repository.get_classroom_by_operation_id(operation_id)
        if existing is not None:
            if existing.id != classroom_id:
                raise ClassroomOperationConflictError()
            return existing
        current = self._required_classroom(classroom_id)
        if not current.is_active:
            return current
        updated = replace(
            current,
            is_active=False,
            updated_at=self._clock(),
            version=current.version + 1,
            last_operation_id=operation_id,
            operation_ids=self._append_operation(current.operation_ids, operation_id),
        )
        saved = self._repository.replace_classroom(updated, expected_version=expected_version)
        if saved is None:
            raise ClassroomConcurrentUpdateError()
        self._audit_change(
            operation_id=operation_id,
            actor=actor,
            action="CLASSROOM_DEACTIVATED",
            resource_type="classroom",
            resource_id=saved.id,
            before=self._classroom_audit(current),
            after=self._classroom_audit(saved),
            ip_fingerprint=ip_fingerprint,
        )
        return saved

    def replace_schedules(
        self,
        actor: User,
        command: ReplaceSchedulesCommand,
        *,
        ip_fingerprint: str | None,
    ) -> Classroom:
        self._require_admin(actor)
        operation_id = self._operation_id(command.operation_id)
        schedules = self._schedules(command.schedules)
        existing = self._repository.get_classroom_by_operation_id(operation_id)
        if existing is not None:
            if existing.id != command.classroom_id or existing.schedules != schedules:
                raise ClassroomOperationConflictError()
            return existing
        current = self._required_classroom(command.classroom_id)
        updated = replace(
            current,
            schedules=schedules,
            updated_at=self._clock(),
            version=current.version + 1,
            last_operation_id=operation_id,
            operation_ids=self._append_operation(current.operation_ids, operation_id),
        )
        saved = self._repository.replace_classroom(
            updated, expected_version=command.expected_version
        )
        if saved is None:
            raise ClassroomConcurrentUpdateError()
        self._audit_change(
            operation_id=operation_id,
            actor=actor,
            action="CLASSROOM_SCHEDULES_REPLACED",
            resource_type="classroom",
            resource_id=saved.id,
            before={"schedules": self._schedule_audit(current.schedules)},
            after={"schedules": self._schedule_audit(saved.schedules)},
            ip_fingerprint=ip_fingerprint,
        )
        return saved

    def create_seat(
        self,
        actor: User,
        command: CreateSeatCommand,
        *,
        ip_fingerprint: str | None,
    ) -> Seat:
        self._require_admin(actor)
        classroom = self._required_active_classroom(command.classroom_id)
        operation_id = self._operation_id(command.operation_id)
        code = self._code(command.code)
        label = self._text(command.label, "좌석 label")
        geometry = self._geometry(command.geometry)
        existing = self._repository.get_seat_by_operation_id(operation_id)
        if existing is not None:
            if (
                existing.classroom_id != classroom.id
                or existing.code != code
                or existing.label != label
                or existing.geometry != geometry
            ):
                raise ClassroomOperationConflictError()
            return existing
        now = self._clock()
        seat = Seat(
            id=str(uuid4()),
            classroom_id=classroom.id,
            code=code,
            label=label,
            geometry=geometry,
            is_active=True,
            current_occupancy=SeatCurrentOccupancy(
                state=SeatOccupancy.UNKNOWN,
                source=OccupancySource.SYSTEM,
                confidence=None,
                observed_at=None,
                event_id=None,
            ),
            created_at=now,
            updated_at=now,
            version=0,
            created_operation_id=operation_id,
            last_operation_id=operation_id,
            operation_ids=(operation_id,),
        )
        saved = self._repository.create_seat(seat)
        self._audit_change(
            operation_id=operation_id,
            actor=actor,
            action="SEAT_CREATED",
            resource_type="seat",
            resource_id=saved.id,
            before=None,
            after=self._seat_audit(saved),
            ip_fingerprint=ip_fingerprint,
        )
        return saved

    def list_seats(
        self,
        actor: User,
        classroom_id: str,
        *,
        include_inactive: bool,
        limit: int,
        offset: int,
    ) -> SeatPage:
        self.get_classroom(actor, classroom_id)
        if include_inactive and actor.role not in ADMIN_ROLES:
            include_inactive = False
        return self._repository.list_seats(
            classroom_id,
            include_inactive=include_inactive,
            limit=limit,
            offset=offset,
        )

    def update_seat(
        self,
        actor: User,
        command: UpdateSeatCommand,
        *,
        ip_fingerprint: str | None,
    ) -> Seat:
        self._require_admin(actor)
        operation_id = self._operation_id(command.operation_id)
        code = self._code(command.code)
        label = self._text(command.label, "좌석 label")
        geometry = self._geometry(command.geometry)
        existing = self._repository.get_seat_by_operation_id(operation_id)
        if existing is not None:
            if (
                existing.id != command.seat_id
                or existing.code != code
                or existing.label != label
                or existing.geometry != geometry
            ):
                raise ClassroomOperationConflictError()
            return existing
        current = self._required_seat(command.seat_id)
        updated = replace(
            current,
            code=code,
            label=label,
            geometry=geometry,
            updated_at=self._clock(),
            version=current.version + 1,
            last_operation_id=operation_id,
            operation_ids=self._append_operation(current.operation_ids, operation_id),
        )
        saved = self._repository.replace_seat(updated, expected_version=command.expected_version)
        if saved is None:
            raise ClassroomConcurrentUpdateError()
        self._audit_change(
            operation_id=operation_id,
            actor=actor,
            action="SEAT_UPDATED",
            resource_type="seat",
            resource_id=saved.id,
            before=self._seat_audit(current),
            after=self._seat_audit(saved),
            ip_fingerprint=ip_fingerprint,
        )
        return saved

    def deactivate_seat(
        self,
        actor: User,
        seat_id: str,
        *,
        expected_version: int,
        operation_id: str,
        ip_fingerprint: str | None,
    ) -> Seat:
        self._require_admin(actor)
        operation_id = self._operation_id(operation_id)
        existing = self._repository.get_seat_by_operation_id(operation_id)
        if existing is not None:
            if existing.id != seat_id:
                raise ClassroomOperationConflictError()
            return existing
        current = self._required_seat(seat_id)
        if not current.is_active:
            return current
        updated = replace(
            current,
            is_active=False,
            updated_at=self._clock(),
            version=current.version + 1,
            last_operation_id=operation_id,
            operation_ids=self._append_operation(current.operation_ids, operation_id),
        )
        saved = self._repository.replace_seat(updated, expected_version=expected_version)
        if saved is None:
            raise ClassroomConcurrentUpdateError()
        self._audit_change(
            operation_id=operation_id,
            actor=actor,
            action="SEAT_DEACTIVATED",
            resource_type="seat",
            resource_id=saved.id,
            before=self._seat_audit(current),
            after=self._seat_audit(saved),
            ip_fingerprint=ip_fingerprint,
        )
        return saved

    def occupancy_summary(self, actor: User, classroom_id: str) -> ClassroomOccupancySummary:
        classroom = self.get_classroom(actor, classroom_id)
        page = self._repository.list_seats(
            classroom.id, include_inactive=False, limit=200, offset=0
        )
        states = [seat.current_occupancy.state for seat in page.items]
        observed = [
            seat.current_occupancy.observed_at
            for seat in page.items
            if seat.current_occupancy.observed_at is not None
        ]
        return ClassroomOccupancySummary(
            classroom=classroom,
            seats=page.items,
            total=page.total,
            occupied_count=states.count(SeatOccupancy.OCCUPIED),
            vacant_count=states.count(SeatOccupancy.VACANT),
            unknown_count=states.count(SeatOccupancy.UNKNOWN),
            is_operating=self._is_operating(classroom, self._clock()),
            last_observed_at=max(observed) if observed else None,
        )

    def list_occupancy_history(
        self,
        actor: User,
        classroom_id: str,
        *,
        seat_id: str | None,
        from_time: datetime | None,
        to_time: datetime | None,
        limit: int,
        offset: int,
    ) -> SeatOccupancyHistoryPage:
        self._require_admin(actor)
        self._required_classroom(classroom_id)
        if from_time is not None and to_time is not None and from_time >= to_time:
            raise ClassroomInputError("조회 시작 시각은 종료 시각보다 빨라야 합니다.")
        return self._repository.list_occupancy_history(
            classroom_id,
            seat_id=seat_id,
            from_time=from_time,
            to_time=to_time,
            limit=limit,
            offset=offset,
        )

    def record_mock_observation_batch(
        self,
        actor: User,
        command: RecordSeatObservationBatchCommand,
    ) -> SeatObservationBatchResult:
        self._require_admin(actor)
        classroom = self._required_classroom(command.classroom_id)
        event_id = self._operation_id(command.event_id)
        observed_at = self._aware_datetime(command.observed_at)
        observations = self._observations(command.observations)
        existing_batch = self._repository.get_observation_batch(event_id)
        if existing_batch is not None:
            if (
                existing_batch.classroom_id != classroom.id
                or existing_batch.observed_at != observed_at
                or existing_batch.observations != observations
            ):
                raise SeatBatchConflictError()
            if existing_batch.status == ObservationBatchStatus.COMPLETED:
                return self._batch_result(existing_batch)
        elif not classroom.is_active:
            raise ClassroomInputError("비활성 강의실은 변경할 수 없습니다.")
        seats: dict[str, Seat] = {}
        for item in observations:
            seat = self._repository.get_seat(item.seat_id)
            if seat is None:
                raise ClassroomInputError(
                    "batch의 모든 좌석은 요청한 강의실 소속의 활성 좌석이어야 합니다."
                )
            seats[item.seat_id] = seat
        if any(seat.classroom_id != classroom.id for seat in seats.values()) or (
            existing_batch is None and any(not seat.is_active for seat in seats.values())
        ):
            raise ClassroomInputError(
                "batch의 모든 좌석은 요청한 강의실 소속의 활성 좌석이어야 합니다."
            )
        claimed = existing_batch
        if claimed is None:
            received_at = self._clock()
            claimed = self._repository.claim_observation_batch(
                SeatObservationBatchRecord(
                    event_id=event_id,
                    classroom_id=classroom.id,
                    actor_user_id=actor.id,
                    observed_at=observed_at,
                    observations=observations,
                    status=ObservationBatchStatus.PROCESSING,
                    processed_count=0,
                    changed_count=0,
                    alert_count=0,
                    received_at=received_at,
                    completed_at=None,
                )
            )
        received_at = claimed.received_at
        if claimed.status == ObservationBatchStatus.COMPLETED:
            return self._batch_result(claimed)

        histories: list[SeatOccupancyHistory] = []
        alert_count = 0
        for observation in observations:
            history = self._apply_observation(
                classroom,
                observation,
                event_id=event_id,
                observed_at=observed_at,
                received_at=received_at,
            )
            histories.append(history)
            current = self._required_seat(observation.seat_id)
            if (
                history.applied_to_current
                and history.state_changed
                and history.to_state == SeatOccupancy.OCCUPIED
                and current.current_occupancy.event_id == event_id
                and self._is_after_hours(classroom, observed_at)
            ):
                alert, belongs_to_event = self._ensure_after_hours_alert(
                    actor,
                    classroom,
                    current,
                    event_id=event_id,
                    observed_at=observed_at,
                )
                if belongs_to_event:
                    alert_count += 1
                self._ensure_alert_notifications(classroom, current, alert)

        completed = replace(
            claimed,
            status=ObservationBatchStatus.COMPLETED,
            processed_count=len(histories),
            changed_count=sum(item.state_changed for item in histories),
            alert_count=alert_count,
            completed_at=self._clock(),
        )
        return self._batch_result(self._repository.complete_observation_batch(completed))

    def list_alerts(
        self,
        actor: User,
        *,
        status: AfterHoursAlertStatus | None,
        classroom_id: str | None,
        business_date: date | None,
        limit: int,
        offset: int,
    ) -> AfterHoursAlertPage:
        self._require_admin(actor)
        return self._repository.list_alerts(
            status=status,
            classroom_id=classroom_id,
            business_date=business_date,
            limit=limit,
            offset=offset,
        )

    def resolve_alert(
        self,
        actor: User,
        command: ResolveAfterHoursAlertCommand,
        *,
        ip_fingerprint: str | None,
    ) -> AfterHoursAlert:
        self._require_admin(actor)
        operation_id = self._operation_id(command.operation_id)
        existing = self._repository.get_alert_by_operation_id(operation_id)
        if existing is not None:
            if existing.id != command.alert_id:
                raise ClassroomOperationConflictError()
            return existing
        current = self._required_alert(command.alert_id)
        if current.status == AfterHoursAlertStatus.RESOLVED:
            return current
        if current.status != AfterHoursAlertStatus.OPEN:
            raise AfterHoursAlertTransitionError()
        updated = replace(
            current,
            status=AfterHoursAlertStatus.RESOLVED,
            resolved_at=self._clock(),
            resolved_by_user_id=actor.id,
            last_operation_id=operation_id,
            operation_ids=self._append_operation(current.operation_ids, operation_id),
            version=current.version + 1,
        )
        saved = self._repository.replace_alert(updated, expected_version=command.expected_version)
        if saved is None:
            raise ClassroomConcurrentUpdateError()
        self._audit_change(
            operation_id=operation_id,
            actor=actor,
            action="AFTER_HOURS_ALERT_RESOLVED",
            resource_type="after_hours_alert",
            resource_id=saved.id,
            before={"status": current.status.value},
            after={"status": saved.status.value},
            ip_fingerprint=ip_fingerprint,
        )
        return saved

    def _apply_observation(
        self,
        classroom: Classroom,
        observation: SeatObservation,
        *,
        event_id: str,
        observed_at: datetime,
        received_at: datetime,
    ) -> SeatOccupancyHistory:
        existing = self._repository.get_history_by_event_and_seat(event_id, observation.seat_id)
        if existing is not None:
            self._repair_current_from_history(existing)
            return existing
        seat = self._required_seat(observation.seat_id)
        current = seat.current_occupancy
        target = self._occupancy(observation)
        applied = current.observed_at is None or observed_at >= current.observed_at
        history = SeatOccupancyHistory(
            id=str(
                uuid5(
                    NAMESPACE_URL,
                    f"seat-occupancy-history:{event_id}:{observation.seat_id}",
                )
            ),
            seat_id=seat.id,
            classroom_id=classroom.id,
            event_id=event_id,
            from_state=current.state,
            to_state=target,
            occupied=observation.occupied,
            confidence=observation.confidence,
            observed_at=observed_at,
            received_at=received_at,
            applied_to_current=applied,
            state_changed=applied and current.state != target,
        )
        stored = self._repository.append_occupancy_history(history)
        self._repair_current_from_history(stored)
        return stored

    def _repair_current_from_history(self, history: SeatOccupancyHistory) -> None:
        if not history.applied_to_current:
            return
        operation_id = f"seat-observation:{history.event_id}:{history.seat_id}"
        for _ in range(3):
            current = self._required_seat(history.seat_id)
            if current.current_occupancy.event_id == history.event_id:
                return
            if (
                current.current_occupancy.observed_at is not None
                and current.current_occupancy.observed_at > history.observed_at
            ):
                return
            updated = replace(
                current,
                current_occupancy=SeatCurrentOccupancy(
                    state=history.to_state,
                    source=OccupancySource.MOCK,
                    confidence=history.confidence,
                    observed_at=history.observed_at,
                    event_id=history.event_id,
                ),
                updated_at=self._clock(),
                version=current.version + 1,
                last_operation_id=operation_id,
                operation_ids=self._append_operation(current.operation_ids, operation_id),
            )
            saved = self._repository.replace_seat(updated, expected_version=current.version)
            if saved is not None:
                return
        raise ClassroomConcurrentUpdateError()

    def _ensure_after_hours_alert(
        self,
        actor: User,
        classroom: Classroom,
        seat: Seat,
        *,
        event_id: str,
        observed_at: datetime,
    ) -> tuple[AfterHoursAlert, bool]:
        business_date = observed_at.astimezone(ZoneInfo(classroom.timezone)).date()
        dedupe_key = f"{classroom.id}:{seat.id}:{business_date.isoformat()}:after_hours"
        operation_id = f"after-hours-alert:{event_id}:{seat.id}"
        alert = AfterHoursAlert(
            id=str(uuid5(NAMESPACE_URL, f"after-hours-alert:{dedupe_key}")),
            dedupe_key=dedupe_key,
            classroom_id=classroom.id,
            seat_id=seat.id,
            business_date=business_date,
            status=AfterHoursAlertStatus.OPEN,
            detected_at=observed_at,
            resolved_at=None,
            resolved_by_user_id=None,
            created_operation_id=operation_id,
            last_operation_id=operation_id,
            operation_ids=(operation_id,),
            version=0,
        )
        stored, _ = self._repository.create_alert(alert)
        return stored, stored.created_operation_id == operation_id

    def _ensure_alert_notifications(
        self, classroom: Classroom, seat: Seat, alert: AfterHoursAlert
    ) -> None:
        if alert.status != AfterHoursAlertStatus.OPEN:
            return
        recipients = {
            user.id: user
            for user in self._active_users(UserRole.STAFF)
            if user.id in classroom.responsible_staff_user_ids
        }
        recipients.update({user.id: user for user in self._active_users(UserRole.ADMIN)})
        for recipient in recipients.values():
            dedupe_key = f"after_hours_seat:{alert.id}:{recipient.id}"
            self._notifications.create(
                CreateNotificationCommand(
                    recipient_user_id=recipient.id,
                    type="AFTER_HOURS_SEAT",
                    title="마감 후 점유 좌석이 확인됐습니다",
                    body=f"{classroom.name} {seat.label} 좌석을 확인해 주세요.",
                    data={
                        "target_route": (
                            "/admin"
                            if recipient.role == UserRole.ADMIN
                            else f"/classrooms/{classroom.id}"
                        ),
                        "alert_id": alert.id,
                        "classroom_id": classroom.id,
                        "seat_id": seat.id,
                    },
                    operation_id=f"after-hours-seat-notification:{alert.id}:{recipient.id}",
                    dedupe_key=dedupe_key,
                )
            )

    def list_responsible_staff_candidates(self, actor: User) -> list[User]:
        self._require_admin(actor)
        return self._active_users(UserRole.STAFF)

    def _active_users(self, role: UserRole) -> list[User]:
        users: list[User] = []
        offset = 0
        page_size = 200
        while True:
            page = self._users.list_users(
                limit=page_size,
                offset=offset,
                role=role,
                status=UserStatus.ACTIVE,
                search=None,
            )
            users.extend(page.items)
            offset += len(page.items)
            if offset >= page.total or not page.items:
                return users

    def _responsible_staff_user_ids(self, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) > 50:
            raise ClassroomInputError("담당 직원은 최대 50명까지 지정할 수 있습니다.")
        normalized = tuple(sorted({value.strip() for value in values if value.strip()}))
        if len(normalized) != len(values):
            raise ClassroomInputError("담당 직원 식별자가 올바르지 않습니다.")
        for user_id in normalized:
            user = self._users.get_user(user_id)
            if (
                user is None
                or user.role != UserRole.STAFF
                or user.status != UserStatus.ACTIVE
            ):
                raise ClassroomInputError("담당 직원은 활성 STAFF 계정이어야 합니다.")
        return normalized

    def _is_operating(self, classroom: Classroom, instant: datetime) -> bool:
        local = instant.astimezone(ZoneInfo(classroom.timezone))
        schedule = next(
            (item for item in classroom.schedules if item.day_of_week == local.weekday()),
            None,
        )
        return bool(
            schedule
            and schedule.opens_at <= local.timetz().replace(tzinfo=None) < schedule.closes_at
        )

    def _is_after_hours(self, classroom: Classroom, instant: datetime) -> bool:
        local = instant.astimezone(ZoneInfo(classroom.timezone))
        schedule = next(
            (item for item in classroom.schedules if item.day_of_week == local.weekday()),
            None,
        )
        if schedule is None:
            return True
        closing = datetime.combine(
            local.date(), schedule.closes_at, tzinfo=ZoneInfo(classroom.timezone)
        ) + timedelta(minutes=classroom.after_hours_grace_minutes)
        return local >= closing

    def _required_classroom(self, classroom_id: str) -> Classroom:
        classroom = self._repository.get_classroom(classroom_id)
        if classroom is None:
            raise ClassroomNotFoundError()
        return classroom

    def _required_active_classroom(self, classroom_id: str) -> Classroom:
        classroom = self._required_classroom(classroom_id)
        if not classroom.is_active:
            raise ClassroomInputError("비활성 강의실은 변경할 수 없습니다.")
        return classroom

    def _required_seat(self, seat_id: str) -> Seat:
        seat = self._repository.get_seat(seat_id)
        if seat is None:
            raise SeatNotFoundError()
        return seat

    def _required_alert(self, alert_id: str) -> AfterHoursAlert:
        alert = self._repository.get_alert(alert_id)
        if alert is None:
            raise AfterHoursAlertNotFoundError()
        return alert

    @staticmethod
    def _require_active(actor: User) -> None:
        if actor.status != UserStatus.ACTIVE:
            raise PermissionDeniedError()

    @staticmethod
    def _require_admin(actor: User) -> None:
        if actor.status != UserStatus.ACTIVE or actor.role not in ADMIN_ROLES:
            raise PermissionDeniedError()

    @staticmethod
    def _text(value: str, label: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 200:
            raise ClassroomInputError(f"{label} 값이 올바르지 않습니다.")
        return normalized

    @staticmethod
    def _code(value: str) -> str:
        normalized = value.strip().upper()
        if not normalized or len(normalized) > 64:
            raise ClassroomInputError("code 값이 올바르지 않습니다.")
        return normalized

    @staticmethod
    def _operation_id(value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 128:
            raise ClassroomInputError("작업 식별자가 올바르지 않습니다.")
        return normalized

    @staticmethod
    def _timezone(value: str) -> str:
        normalized = value.strip()
        try:
            ZoneInfo(normalized)
        except (ValueError, ZoneInfoNotFoundError):
            raise ClassroomInputError("유효한 IANA timezone이 필요합니다.") from None
        return normalized

    @staticmethod
    def _grace(value: int) -> int:
        if isinstance(value, bool) or value < 0 or value > 1440:
            raise ClassroomInputError("마감 grace는 0~1440분이어야 합니다.")
        return value

    @staticmethod
    def _geometry(value: SeatGeometry | None) -> SeatGeometry | None:
        if value is None:
            return None
        numbers = (value.x, value.y, value.width, value.height)
        if any(not isfinite(number) or number < 0 or number > 1 for number in numbers):
            raise ClassroomInputError("geometry 값은 모두 0~1이어야 합니다.")
        if value.width <= 0 or value.height <= 0:
            raise ClassroomInputError("geometry 크기는 0보다 커야 합니다.")
        if value.x + value.width > 1 or value.y + value.height > 1:
            raise ClassroomInputError("geometry 영역은 정규화 범위를 넘을 수 없습니다.")
        return value

    @staticmethod
    def _schedules(values: tuple[ClassroomSchedule, ...]) -> tuple[ClassroomSchedule, ...]:
        if len(values) > 7 or len({item.day_of_week for item in values}) != len(values):
            raise ClassroomInputError("요일별 일정은 하루에 하나만 등록할 수 있습니다.")
        for item in values:
            if item.day_of_week < 0 or item.day_of_week > 6:
                raise ClassroomInputError("요일은 월요일 0부터 일요일 6까지입니다.")
            if item.closes_at <= item.opens_at:
                raise ClassroomInputError("당일 일정은 종료 시각이 시작 시각보다 늦어야 합니다.")
        return tuple(sorted(values, key=lambda item: item.day_of_week))

    @staticmethod
    def _observations(values: tuple[SeatObservation, ...]) -> tuple[SeatObservation, ...]:
        if not values or len(values) > 200:
            raise ClassroomInputError("좌석 관측은 1~200개여야 합니다.")
        if len({item.seat_id for item in values}) != len(values):
            raise ClassroomInputError("한 batch에 같은 좌석을 중복할 수 없습니다.")
        for item in values:
            if (
                not item.seat_id.strip()
                or not isfinite(item.confidence)
                or item.confidence < 0
                or item.confidence > 1
            ):
                raise ClassroomInputError("좌석 관측 값이 올바르지 않습니다.")
        return tuple(sorted(values, key=lambda item: item.seat_id))

    def _occupancy(self, observation: SeatObservation) -> SeatOccupancy:
        if observation.confidence < self._threshold:
            return SeatOccupancy.UNKNOWN
        return SeatOccupancy.OCCUPIED if observation.occupied else SeatOccupancy.VACANT

    @staticmethod
    def _aware_datetime(value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ClassroomInputError("observed_at은 timezone을 포함해야 합니다.")
        return value.astimezone(UTC)

    @staticmethod
    def _append_operation(values: tuple[str, ...], operation_id: str) -> tuple[str, ...]:
        return values if operation_id in values else (*values, operation_id)

    @staticmethod
    def _batch_result(record: SeatObservationBatchRecord) -> SeatObservationBatchResult:
        return SeatObservationBatchResult(
            event_id=record.event_id,
            processed_count=record.processed_count,
            changed_count=record.changed_count,
            alert_count=record.alert_count,
        )

    def _audit_change(
        self,
        *,
        operation_id: str,
        actor: User,
        action: str,
        resource_type: str,
        resource_id: str,
        before: Mapping[str, object] | None,
        after: Mapping[str, object] | None,
        ip_fingerprint: str | None,
    ) -> None:
        self._audit.record(
            operation_id=f"classroom-audit:{operation_id}",
            actor_user_id=actor.id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            before=before,
            after=after,
            ip_fingerprint=ip_fingerprint,
        )

    @staticmethod
    def _classroom_audit(item: Classroom) -> dict[str, object]:
        return {
            "code": item.code,
            "name": item.name,
            "location": item.location,
            "timezone": item.timezone,
            "after_hours_grace_minutes": item.after_hours_grace_minutes,
            "responsible_staff_user_ids": list(item.responsible_staff_user_ids),
            "is_active": item.is_active,
            "version": item.version,
        }

    @staticmethod
    def _seat_audit(item: Seat) -> dict[str, object]:
        return {
            "classroom_id": item.classroom_id,
            "code": item.code,
            "label": item.label,
            "geometry": (
                None
                if item.geometry is None
                else {
                    "x": item.geometry.x,
                    "y": item.geometry.y,
                    "width": item.geometry.width,
                    "height": item.geometry.height,
                }
            ),
            "is_active": item.is_active,
            "version": item.version,
        }

    @staticmethod
    def _schedule_audit(schedules: tuple[ClassroomSchedule, ...]) -> list[dict[str, object]]:
        return [
            {
                "day_of_week": item.day_of_week,
                "opens_at": item.opens_at.isoformat(),
                "closes_at": item.closes_at.isoformat(),
            }
            for item in schedules
        ]
