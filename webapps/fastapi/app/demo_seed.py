"""local memory mode에서 제품 흐름을 채우는 결정적 demo fixture."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from .classrooms.models import (
    AfterHoursAlertStatus,
    ClassroomSchedule,
    CreateClassroomCommand,
    CreateSeatCommand,
    RecordSeatObservationBatchCommand,
    ReplaceSchedulesCommand,
    ResolveAfterHoursAlertCommand,
    SeatGeometry,
    SeatObservation,
)
from .classrooms.service import ClassroomService
from .employees.models import CreateEmployeeCommand, Employee, RecordEmployeeObservationCommand
from .employees.service import EmployeeService
from .interview_waits.models import CreateInterviewWaitCommand
from .interview_waits.service import InterviewWaitService
from .users.models import User, UserRole


@dataclass(frozen=True)
class DemoSeedServices:
    """demo fixture가 기존 도메인 규칙을 거쳐 저장되도록 묶은 서비스."""

    employees: EmployeeService
    interview_waits: InterviewWaitService
    classrooms: ClassroomService


def seed_demo_data(
    users: list[User],
    services: DemoSeedServices,
    *,
    now: datetime,
) -> None:
    """사용자 외 v2 최소 demo 데이터를 멱등 command로 채운다."""
    if now.tzinfo is None:
        raise ValueError("demo seed 시각은 timezone-aware 값이어야 합니다.")
    student = _single_user(users, UserRole.STUDENT)
    admin = _single_user(users, UserRole.ADMIN)
    staff_users = sorted(
        (user for user in users if user.role == UserRole.STAFF),
        key=lambda user: (user.email, user.id),
    )
    if len(staff_users) < 2:
        raise ValueError("demo seed에는 STAFF 사용자 두 명이 필요합니다.")

    employees = _seed_employees(services.employees, admin, staff_users, now=now)
    _seed_interview_waits(
        services.interview_waits,
        student,
        waiting_employee_id=employees[2].id,
        ready_employee_id=employees[0].id,
    )
    _seed_classrooms(services.classrooms, admin, staff_users, now=now)


def _seed_employees(
    service: EmployeeService,
    admin: User,
    staff_users: list[User],
    *,
    now: datetime,
) -> list[Employee]:
    fixture_rows = (
        ("001", staff_users[0].id, "데모 직원 01", "학생지원", "상담 직원", "A동 1층"),
        ("002", staff_users[1].id, "데모 직원 02", "시설관리", "시설 직원", "B동 2층"),
        ("003", None, "데모 직원 03", "학사운영", "운영 직원", "A동 2층"),
        ("004", None, "데모 직원 04", "안전관리", "안전 직원", "B동 1층"),
    )
    employees = [
        service.create_employee(
            admin,
            CreateEmployeeCommand(
                employee_no=f"DEMO-EMP-{number}",
                user_id=user_id,
                display_name=name,
                department=department,
                position=position,
                office_zone=office_zone,
                operation_id=_operation_id(f"employee-create-{number}"),
                entity_id=_entity_id(f"employee-{number}"),
            ),
            ip_fingerprint=None,
        )
        for number, user_id, name, department, position, office_zone in fixture_rows
    ]

    service.record_mock_observation(
        admin,
        RecordEmployeeObservationCommand(
            event_id=_operation_id("employee-working-observation"),
            employee_id=employees[0].id,
            person_present=True,
            phone_detected=False,
            confidence=0.97,
            observed_at=now - timedelta(minutes=2),
        ),
    )
    service.record_mock_observation(
        admin,
        RecordEmployeeObservationCommand(
            event_id=_operation_id("employee-on-call-observation"),
            employee_id=employees[1].id,
            person_present=True,
            phone_detected=True,
            confidence=0.94,
            observed_at=now - timedelta(minutes=1),
        ),
    )
    service.record_mock_observation(
        admin,
        RecordEmployeeObservationCommand(
            event_id=_operation_id("employee-offsite-present-observation"),
            employee_id=employees[3].id,
            person_present=True,
            phone_detected=False,
            confidence=0.93,
            observed_at=now - timedelta(hours=2),
        ),
    )
    service.record_mock_observation(
        admin,
        RecordEmployeeObservationCommand(
            event_id=_operation_id("employee-offsite-absent-observation"),
            employee_id=employees[3].id,
            person_present=False,
            phone_detected=False,
            confidence=0.91,
            observed_at=now - timedelta(minutes=1),
        ),
    )
    return employees


def _seed_interview_waits(
    service: InterviewWaitService,
    student: User,
    *,
    waiting_employee_id: str,
    ready_employee_id: str,
) -> None:
    service.create_wait(
        student,
        CreateInterviewWaitCommand(
            employee_id=waiting_employee_id,
            message="데모 WAITING 면담",
            operation_id=_operation_id("interview-wait-waiting"),
            entity_id=_entity_id("interview-wait-waiting"),
        ),
    )
    service.create_wait(
        student,
        CreateInterviewWaitCommand(
            employee_id=ready_employee_id,
            message="데모 READY 면담",
            operation_id=_operation_id("interview-wait-ready"),
            entity_id=_entity_id("interview-wait-ready"),
        ),
    )


def _seed_classrooms(
    service: ClassroomService,
    admin: User,
    staff_users: list[User],
    *,
    now: datetime,
) -> None:
    classrooms = [
        service.create_classroom(
            admin,
            CreateClassroomCommand(
                code=code,
                name=name,
                location=location,
                timezone="Asia/Seoul",
                after_hours_grace_minutes=10,
                responsible_staff_user_ids=(staff_user.id,),
                operation_id=_operation_id(f"classroom-create-{code.lower()}"),
                entity_id=_entity_id(f"classroom-{code.lower()}"),
            ),
            ip_fingerprint=None,
        )
        for code, name, location, staff_user in (
            ("A101", "A101 일반 강의실", "A동 1층", staff_users[0]),
            ("B203", "B203 실습실", "B동 2층", staff_users[1]),
        )
    ]
    schedules = tuple(
        ClassroomSchedule(day_of_week=day, opens_at=time(9), closes_at=time(18)) for day in range(7)
    )
    for classroom in classrooms:
        classroom = service.replace_schedules(
            admin,
            ReplaceSchedulesCommand(
                classroom_id=classroom.id,
                schedules=schedules,
                expected_version=classroom.version,
                operation_id=_operation_id(f"classroom-schedules-{classroom.code.lower()}"),
            ),
            ip_fingerprint=None,
        )
        seats = [
            service.create_seat(
                admin,
                CreateSeatCommand(
                    classroom_id=classroom.id,
                    code=f"S0{index}",
                    label=f"좌석 {index}",
                    geometry=(
                        SeatGeometry(
                            x=0.08 + (index - 1) * 0.3,
                            y=0.2,
                            width=0.2,
                            height=0.24,
                        )
                        if classroom.code == "A101"
                        else None
                    ),
                    operation_id=_operation_id(f"seat-create-{classroom.code.lower()}-{index}"),
                    entity_id=_entity_id(f"seat-{classroom.code.lower()}-{index}"),
                ),
                ip_fingerprint=None,
            )
            for index in range(1, 4)
        ]
        observed_at = _previous_demo_evening(now)
        service.record_mock_observation_batch(
            admin,
            RecordSeatObservationBatchCommand(
                event_id=_operation_id(f"seat-observation-{classroom.code.lower()}"),
                classroom_id=classroom.id,
                observed_at=observed_at,
                observations=(
                    SeatObservation(seat_id=seats[0].id, occupied=False, confidence=0.96),
                    SeatObservation(seat_id=seats[1].id, occupied=True, confidence=0.95),
                    SeatObservation(seat_id=seats[2].id, occupied=False, confidence=0.35),
                ),
            ),
        )

    b203_alerts = service.list_alerts(
        admin,
        status=None,
        classroom_id=classrooms[1].id,
        business_date=None,
        limit=50,
        offset=0,
    )
    b203_alert = next(
        alert
        for alert in b203_alerts.items
        if alert.status in {AfterHoursAlertStatus.OPEN, AfterHoursAlertStatus.RESOLVED}
    )
    service.resolve_alert(
        admin,
        ResolveAfterHoursAlertCommand(
            alert_id=b203_alert.id,
            expected_version=b203_alert.version,
            operation_id=_operation_id("after-hours-alert-resolve-b203"),
        ),
        ip_fingerprint=None,
    )


def _previous_demo_evening(now: datetime) -> datetime:
    seoul = ZoneInfo("Asia/Seoul")
    previous_date = (now.astimezone(seoul) - timedelta(days=1)).date()
    return datetime.combine(previous_date, time(20), tzinfo=seoul).astimezone(UTC)


def _single_user(users: list[User], role: UserRole) -> User:
    matches = [user for user in users if user.role == role]
    if len(matches) != 1:
        raise ValueError(f"demo seed에는 {role.value} 사용자 한 명이 필요합니다.")
    return matches[0]


def _operation_id(name: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"smart-office-demo:{name}"))


def _entity_id(name: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"smart-office-demo-entity:{name}"))
