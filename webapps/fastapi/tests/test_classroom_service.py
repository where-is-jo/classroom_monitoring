"""Classroom, seat occupancy, alert, and authorization policy tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from uuid import uuid4

import pytest

from app.audit.service import AuditService
from app.auth.errors import PermissionDeniedError
from app.classrooms.errors import ClassroomInputError, SeatBatchConflictError
from app.classrooms.models import (
    AfterHoursAlertStatus,
    Classroom,
    ClassroomSchedule,
    CreateClassroomCommand,
    RecordSeatObservationBatchCommand,
    ReplaceSchedulesCommand,
    ResolveAfterHoursAlertCommand,
    SeatGeometry,
    SeatObservation,
    SeatObservationBatchResult,
    SeatOccupancy,
    UpdateClassroomCommand,
    UpdateSeatCommand,
)
from app.classrooms.service import ClassroomStaffAssignmentService
from app.users.models import UpdateUserCommand, UserRole, UserStatus
from app.users.service import UserService
from tests.classroom_helpers import ClassroomStack, build_classroom_stack


@pytest.fixture
def stack() -> ClassroomStack:
    return build_classroom_stack()


def _schedule(stack: ClassroomStack, classroom_id: str, *, closes: time = time(17)) -> Classroom:
    classroom = stack.repository.get_classroom(classroom_id)
    assert classroom is not None
    return stack.service.replace_schedules(
        stack.admin,
        ReplaceSchedulesCommand(
            classroom_id=classroom.id,
            schedules=(ClassroomSchedule(day_of_week=2, opens_at=time(9), closes_at=closes),),
            expected_version=classroom.version,
            operation_id=str(uuid4()),
        ),
        ip_fingerprint="test-ip",
    )


def _observe(
    stack: ClassroomStack,
    classroom_id: str,
    seat_id: str,
    *,
    confidence: float,
    occupied: bool = True,
    observed_at: datetime | None = None,
    event_id: str | None = None,
) -> SeatObservationBatchResult:
    return stack.service.record_mock_observation_batch(
        stack.admin,
        RecordSeatObservationBatchCommand(
            event_id=event_id or str(uuid4()),
            classroom_id=classroom_id,
            observed_at=observed_at or stack.auth.clock(),
            observations=(
                SeatObservation(
                    seat_id=seat_id,
                    occupied=occupied,
                    confidence=confidence,
                ),
            ),
        ),
    )


def test_classroom_schedule_seat_geometry_and_permissions(stack: ClassroomStack) -> None:
    classroom = stack.create_classroom()
    seat = stack.create_seat(
        classroom.id,
        geometry=SeatGeometry(x=0.1, y=0.2, width=0.3, height=0.4),
    )
    scheduled = _schedule(stack, classroom.id)

    assert scheduled.schedules[0].day_of_week == 2
    assert seat.current_occupancy.state == SeatOccupancy.UNKNOWN
    assert stack.service.occupancy_summary(stack.student, classroom.id).total == 1
    with pytest.raises(PermissionDeniedError):
        stack.service.update_seat(
            stack.student,
            UpdateSeatCommand(
                seat_id=seat.id,
                code=seat.code,
                label="denied",
                geometry=None,
                expected_version=seat.version,
                operation_id=str(uuid4()),
            ),
            ip_fingerprint="test-ip",
        )
    with pytest.raises(ClassroomInputError):
        stack.service.update_seat(
            stack.admin,
            UpdateSeatCommand(
                seat_id=seat.id,
                code=seat.code,
                label=seat.label,
                geometry=SeatGeometry(x=0.9, y=0.0, width=0.2, height=0.2),
                expected_version=seat.version,
                operation_id=str(uuid4()),
            ),
            ip_fingerprint="test-ip",
        )
    with pytest.raises(ClassroomInputError):
        current = stack.repository.get_classroom(classroom.id)
        assert current is not None
        stack.service.replace_schedules(
            stack.admin,
            ReplaceSchedulesCommand(
                classroom_id=classroom.id,
                schedules=(ClassroomSchedule(2, time(17), time(9)),),
                expected_version=current.version,
                operation_id=str(uuid4()),
            ),
            ip_fingerprint="test-ip",
        )


def test_confidence_boundary_noop_and_old_observation_protect_current(
    stack: ClassroomStack,
) -> None:
    classroom = stack.create_classroom()
    seat = stack.create_seat(classroom.id)
    first_at = datetime(2026, 8, 5, 7, 0, tzinfo=UTC)
    low = _observe(
        stack,
        classroom.id,
        seat.id,
        confidence=0.599,
        observed_at=first_at,
    )
    low_current = stack.repository.get_seat(seat.id)
    assert low.changed_count == 0
    assert low_current is not None
    assert low_current.current_occupancy.state == SeatOccupancy.UNKNOWN

    boundary = _observe(
        stack,
        classroom.id,
        seat.id,
        confidence=0.6,
        observed_at=first_at + timedelta(minutes=1),
    )
    assert boundary.changed_count == 1
    assert stack.repository.get_seat(seat.id).current_occupancy.state == SeatOccupancy.OCCUPIED  # type: ignore[union-attr]

    same = _observe(
        stack,
        classroom.id,
        seat.id,
        confidence=0.9,
        observed_at=first_at + timedelta(minutes=2),
    )
    assert same.changed_count == 0
    current = stack.repository.get_seat(seat.id)
    assert current is not None
    latest_event = current.current_occupancy.event_id

    stale = _observe(
        stack,
        classroom.id,
        seat.id,
        confidence=0.9,
        occupied=False,
        observed_at=first_at - timedelta(minutes=1),
    )
    assert stale.changed_count == 0
    assert stack.repository.get_seat(seat.id).current_occupancy.event_id == latest_event  # type: ignore[union-attr]


def test_classroom_and_seat_update_and_soft_deactivation(stack: ClassroomStack) -> None:
    classroom = stack.create_classroom()
    seat = stack.create_seat(classroom.id)
    classroom = stack.service.update_classroom(
        stack.admin,
        UpdateClassroomCommand(
            classroom_id=classroom.id,
            code="ROOM-UPDATED",
            name="Updated Classroom",
            location="Building C",
            timezone="UTC",
            after_hours_grace_minutes=20,
            expected_version=classroom.version,
            operation_id=str(uuid4()),
        ),
        ip_fingerprint="test-ip",
    )
    seat = stack.service.update_seat(
        stack.admin,
        UpdateSeatCommand(
            seat_id=seat.id,
            code="B-2",
            label="Updated Seat",
            geometry=None,
            expected_version=seat.version,
            operation_id=str(uuid4()),
        ),
        ip_fingerprint="test-ip",
    )
    assert classroom.code == "ROOM-UPDATED"
    assert seat.code == "B-2"

    seat = stack.service.deactivate_seat(
        stack.admin,
        seat.id,
        expected_version=seat.version,
        operation_id=str(uuid4()),
        ip_fingerprint="test-ip",
    )
    classroom = stack.service.deactivate_classroom(
        stack.admin,
        classroom.id,
        expected_version=classroom.version,
        operation_id=str(uuid4()),
        ip_fingerprint="test-ip",
    )
    assert seat.is_active is False
    assert classroom.is_active is False


def test_after_hours_timezone_grace_dedupe_resolve_and_no_reopen(
    stack: ClassroomStack,
) -> None:
    classroom = stack.create_classroom(grace=10)
    _schedule(stack, classroom.id, closes=time(17))
    seat = stack.create_seat(classroom.id)

    before_grace = datetime(2026, 8, 5, 8, 9, 59, tzinfo=UTC)  # 17:09:59 KST
    _observe(
        stack,
        classroom.id,
        seat.id,
        confidence=0.9,
        observed_at=before_grace,
    )
    assert (
        stack.service.list_alerts(
            stack.admin,
            status=None,
            classroom_id=None,
            business_date=None,
            limit=50,
            offset=0,
        ).total
        == 0
    )

    _observe(
        stack,
        classroom.id,
        seat.id,
        occupied=False,
        confidence=0.9,
        observed_at=before_grace + timedelta(seconds=1),
    )
    after = _observe(
        stack,
        classroom.id,
        seat.id,
        confidence=0.9,
        observed_at=before_grace + timedelta(seconds=2),
    )
    assert after.alert_count == 1
    alerts = stack.service.list_alerts(
        stack.admin,
        status=AfterHoursAlertStatus.OPEN,
        classroom_id=classroom.id,
        business_date=datetime(2026, 8, 5, tzinfo=UTC).date(),
        limit=50,
        offset=0,
    )
    assert alerts.total == 1
    assert stack.notifications.count_unread(stack.admin.id) == 1

    alert = alerts.items[0]
    resolved = stack.service.resolve_alert(
        stack.admin,
        ResolveAfterHoursAlertCommand(
            alert_id=alert.id,
            expected_version=alert.version,
            operation_id=str(uuid4()),
        ),
        ip_fingerprint="test-ip",
    )
    assert resolved.status == AfterHoursAlertStatus.RESOLVED

    _observe(
        stack,
        classroom.id,
        seat.id,
        occupied=False,
        confidence=0.9,
        observed_at=before_grace + timedelta(minutes=1),
    )
    repeated = _observe(
        stack,
        classroom.id,
        seat.id,
        confidence=0.9,
        observed_at=before_grace + timedelta(minutes=2),
    )
    assert repeated.alert_count == 0
    assert stack.notifications.count_unread(stack.admin.id) == 1
    assert (
        stack.service.list_alerts(
            stack.admin,
            status=None,
            classroom_id=classroom.id,
            business_date=None,
            limit=50,
            offset=0,
        ).total
        == 1
    )


def test_responsible_staff_validation_and_after_hours_fanout_dedupe(
    stack: ClassroomStack,
) -> None:
    responsible = stack.auth.seed(
        UserRole.STAFF, email="responsible@example.invalid", name="담당 직원"
    )
    other_staff = stack.auth.seed(
        UserRole.STAFF, email="other-staff@example.invalid", name="다른 직원"
    )
    other_admin = stack.auth.seed(
        UserRole.ADMIN, email="other-admin@example.invalid", name="다른 관리자"
    )
    inactive_staff = stack.auth.seed(
        UserRole.STAFF, email="inactive-staff@example.invalid", name="비활성 직원"
    )
    assert stack.auth.users.replace_user(
        replace(inactive_staff, status=UserStatus.INACTIVE, version=inactive_staff.version + 1),
        expected_version=inactive_staff.version,
    )

    with pytest.raises(ClassroomInputError, match="활성 STAFF"):
        stack.service.create_classroom(
            stack.admin,
            CreateClassroomCommand(
                code="INVALID-STUDENT",
                name="Invalid",
                location="Building A",
                timezone="Asia/Seoul",
                after_hours_grace_minutes=0,
                operation_id=str(uuid4()),
                responsible_staff_user_ids=(stack.student.id,),
            ),
            ip_fingerprint="test-ip",
        )
    with pytest.raises(ClassroomInputError, match="활성 STAFF"):
        stack.service.create_classroom(
            stack.admin,
            CreateClassroomCommand(
                code="INVALID-INACTIVE",
                name="Invalid",
                location="Building A",
                timezone="Asia/Seoul",
                after_hours_grace_minutes=0,
                operation_id=str(uuid4()),
                responsible_staff_user_ids=(inactive_staff.id,),
            ),
            ip_fingerprint="test-ip",
        )

    classroom = stack.create_classroom(
        code="FANOUT",
        grace=0,
        responsible_staff_user_ids=(responsible.id,),
    )
    seat = stack.create_seat(classroom.id)
    observed_at = datetime(2026, 8, 5, 8, 0, tzinfo=UTC)
    first = _observe(stack, classroom.id, seat.id, confidence=0.9, observed_at=observed_at)
    assert first.alert_count == 1
    assert stack.notifications.count_unread(responsible.id) == 1
    assert stack.notifications.count_unread(stack.admin.id) == 1
    assert stack.notifications.count_unread(other_admin.id) == 1
    assert stack.notifications.count_unread(other_staff.id) == 0

    _observe(
        stack,
        classroom.id,
        seat.id,
        occupied=False,
        confidence=0.9,
        observed_at=observed_at + timedelta(minutes=1),
    )
    repeated = _observe(
        stack,
        classroom.id,
        seat.id,
        confidence=0.9,
        observed_at=observed_at + timedelta(minutes=2),
    )
    assert repeated.alert_count == 0
    for recipient in (responsible, stack.admin, other_admin):
        page = stack.notifications.list_notifications(
            recipient_user_id=recipient.id,
            is_read=None,
            notification_type=None,
            limit=10,
            offset=0,
        )
        assert page.total == 1
        assert page.items[0].type == "AFTER_HOURS_SEAT"
        assert page.items[0].dedupe_key == (
            f"after_hours_seat:{page.items[0].data['alert_id']}:{recipient.id}"
        )


def test_staff_role_change_unlinks_classroom_assignment(stack: ClassroomStack) -> None:
    responsible = stack.auth.seed(
        UserRole.STAFF, email="role-change@example.invalid", name="역할 변경 직원"
    )
    classroom = stack.create_classroom(
        code="ROLE-CHANGE", responsible_staff_user_ids=(responsible.id,)
    )
    assignment_policy = ClassroomStaffAssignmentService(
        stack.repository,
        AuditService(stack.auth.audit, clock=stack.auth.clock),
        clock=stack.auth.clock,
    )
    stack.auth.user_service = UserService(
        stack.auth.users,
        stack.auth.auth,
        AuditService(stack.auth.audit, clock=stack.auth.clock),
        stack.auth.passwords,
        password_min_length=12,
        clock=stack.auth.clock,
        staff_assignment_policy=assignment_policy,
    )
    command = UpdateUserCommand(
        user_id=responsible.id,
        expected_version=responsible.version,
        operation_id=str(uuid4()),
        role=UserRole.STUDENT,
    )

    stack.auth.user_service.update_user(stack.admin, command, ip_fingerprint="test-ip")
    stack.auth.user_service.update_user(stack.admin, command, ip_fingerprint="test-ip")

    updated = stack.repository.get_classroom(classroom.id)
    assert updated is not None
    assert updated.responsible_staff_user_ids == ()
    assert updated.version == classroom.version + 1


def test_batch_membership_active_validation_idempotency_and_conflict(
    stack: ClassroomStack,
) -> None:
    classroom = stack.create_classroom(code="ROOM-A")
    other = stack.create_classroom(code="ROOM-B")
    seat = stack.create_seat(classroom.id)
    other_seat = stack.create_seat(other.id, code="B-1")
    event_id = str(uuid4())
    command = RecordSeatObservationBatchCommand(
        event_id=event_id,
        classroom_id=classroom.id,
        observed_at=stack.auth.clock(),
        observations=(SeatObservation(seat.id, True, 0.9),),
    )

    first = stack.service.record_mock_observation_batch(stack.admin, command)
    repeated = stack.service.record_mock_observation_batch(stack.admin, command)
    assert repeated == first
    history = stack.repository.list_occupancy_history(
        classroom.id,
        seat_id=None,
        from_time=None,
        to_time=None,
        limit=50,
        offset=0,
    )
    assert history.total == 1

    with pytest.raises(SeatBatchConflictError):
        stack.service.record_mock_observation_batch(
            stack.admin,
            RecordSeatObservationBatchCommand(
                event_id=event_id,
                classroom_id=classroom.id,
                observed_at=stack.auth.clock(),
                observations=(SeatObservation(seat.id, False, 0.9),),
            ),
        )
    with pytest.raises(ClassroomInputError):
        stack.service.record_mock_observation_batch(
            stack.admin,
            RecordSeatObservationBatchCommand(
                event_id=str(uuid4()),
                classroom_id=classroom.id,
                observed_at=stack.auth.clock(),
                observations=(SeatObservation(other_seat.id, True, 0.9),),
            ),
        )
    with pytest.raises(ClassroomInputError):
        stack.service.record_mock_observation_batch(
            stack.admin,
            RecordSeatObservationBatchCommand(
                event_id=str(uuid4()),
                classroom_id=classroom.id,
                observed_at=stack.auth.clock(),
                observations=(SeatObservation("unknown-seat", True, 0.9),),
            ),
        )
    current_seat = stack.repository.get_seat(seat.id)
    assert current_seat is not None
    stack.service.deactivate_seat(
        stack.admin,
        seat.id,
        expected_version=current_seat.version,
        operation_id=str(uuid4()),
        ip_fingerprint="test-ip",
    )
    assert stack.service.record_mock_observation_batch(stack.admin, command) == first
    current_classroom = stack.repository.get_classroom(classroom.id)
    assert current_classroom is not None
    stack.service.deactivate_classroom(
        stack.admin,
        classroom.id,
        expected_version=current_classroom.version,
        operation_id=str(uuid4()),
        ip_fingerprint="test-ip",
    )
    assert stack.service.record_mock_observation_batch(stack.admin, command) == first


def test_timezone_conversion_sets_local_business_date(stack: ClassroomStack) -> None:
    classroom = stack.create_classroom(grace=0, timezone="America/Los_Angeles")
    current = stack.repository.get_classroom(classroom.id)
    assert current is not None
    stack.service.replace_schedules(
        stack.admin,
        ReplaceSchedulesCommand(
            classroom_id=classroom.id,
            schedules=(ClassroomSchedule(1, time(9), time(17)),),
            expected_version=current.version,
            operation_id=str(uuid4()),
        ),
        ip_fingerprint="test-ip",
    )
    seat = stack.create_seat(classroom.id)

    _observe(
        stack,
        classroom.id,
        seat.id,
        confidence=0.9,
        observed_at=datetime(2026, 8, 5, 1, 0, tzinfo=UTC),  # Aug 4 18:00 PDT
    )
    alerts = stack.service.list_alerts(
        stack.admin,
        status=None,
        classroom_id=classroom.id,
        business_date=None,
        limit=50,
        offset=0,
    )
    assert alerts.items[0].business_date.isoformat() == "2026-08-04"
