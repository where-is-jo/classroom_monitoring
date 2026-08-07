"""실제 MongoDB가 제공될 때만 실행하는 연결·index 통합 테스트."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.admin.adapters.mongo_repository import MongoAdminDashboardRepository
from app.audit.adapters.mongo_repository import MongoAuditRepository
from app.audit.models import AuditLog
from app.auth.adapters.mongo_repository import MongoAuthRepository
from app.auth.models import RefreshRotationStatus, RefreshToken
from app.classrooms.adapters.mongo_repository import MongoClassroomRepository
from app.classrooms.models import (
    AfterHoursAlert,
    AfterHoursAlertStatus,
    Classroom,
    ClassroomSchedule,
    ObservationBatchStatus,
    OccupancySource,
    Seat,
    SeatCurrentOccupancy,
    SeatObservation,
    SeatObservationBatchRecord,
    SeatOccupancy,
    SeatOccupancyHistory,
)
from app.employees.adapters.mongo_repository import MongoEmployeeRepository
from app.employees.models import (
    Employee,
    EmployeeCurrentStatus,
    EmployeeObservation,
    EmployeeStatus,
    EmployeeStatusHistory,
    StatusSource,
)
from app.events.adapters.mongo_repository import MongoEventRepository
from app.interview_waits.adapters.mongo_repository import MongoInterviewWaitRepository
from app.interview_waits.models import (
    InterviewWait,
    InterviewWaitHistory,
    InterviewWaitStatus,
)
from app.notifications.adapters.mongo_repository import MongoNotificationRepository
from app.notifications.models import MockDelivery, MockDeliveryStatus, Notification
from app.shared.database import MongoDatabase, initialize_indexes
from app.users.adapters.mongo_repository import MongoUserRepository
from app.users.models import User, UserRole, UserStatus

pytestmark = pytest.mark.mongodb


def test_ping과_index_초기화를_반복해도_같은_index를_유지한다(
    mongodb_database: MongoDatabase,
) -> None:
    mongodb_database.command("ping")

    initializers = [
        MongoEventRepository.ensure_indexes,
        MongoUserRepository.ensure_indexes,
        MongoAuthRepository.ensure_indexes,
        MongoAuditRepository.ensure_indexes,
        MongoEmployeeRepository.ensure_indexes,
        MongoNotificationRepository.ensure_indexes,
        MongoInterviewWaitRepository.ensure_indexes,
        MongoClassroomRepository.ensure_indexes,
        MongoAdminDashboardRepository.ensure_indexes,
    ]
    initialize_indexes(mongodb_database, initializers)
    initialize_indexes(mongodb_database, initializers)

    index_information = mongodb_database["events"].index_information()
    assert MongoEventRepository.detected_at_index_name in index_information
    matching_names = [
        name for name in index_information if name == MongoEventRepository.detected_at_index_name
    ]
    assert matching_names == [MongoEventRepository.detected_at_index_name]
    assert "users_email_unique" in mongodb_database["users"].index_information()
    assert "refresh_tokens_hash_unique" in mongodb_database["refresh_tokens"].index_information()
    assert "audit_logs_operation_unique" in mongodb_database["audit_logs"].index_information()
    assert "employees_number_unique" in mongodb_database["employees"].index_information()
    assert "notifications_dedupe_unique" in mongodb_database["notifications"].index_information()
    assert (
        "notification_deliveries_notification_attempt_unique"
        in mongodb_database["notification_deliveries"].index_information()
    )
    assert (
        "interview_waits_active_key_unique"
        in mongodb_database["interview_waits"].index_information()
    )
    assert (
        "interview_wait_history_operation_unique"
        in mongodb_database["interview_wait_history"].index_information()
    )
    assert "classrooms_code_unique" in mongodb_database["classrooms"].index_information()
    assert "seats_classroom_code_unique" in mongodb_database["seats"].index_information()
    assert (
        "after_hours_alerts_dedupe_unique"
        in mongodb_database["after_hours_alerts"].index_information()
    )
    assert (
        "seat_history_dashboard_recent"
        in mongodb_database["seat_occupancy_history"].index_information()
    )


def test_dashboard_recent_query_explain_uses_bounded_sort_index(
    mongodb_database: MongoDatabase,
) -> None:
    initialize_indexes(mongodb_database, [MongoAdminDashboardRepository.ensure_indexes])
    plan = (
        mongodb_database["seat_occupancy_history"]
        .find(
            {
                "observed_at": {"$gte": datetime.now(UTC) - timedelta(days=1)},
                "state_changed": True,
            }
        )
        .sort([("observed_at", -1), ("_id", 1)])
        .hint("seat_history_dashboard_recent")
        .limit(50)
        .explain()
    )

    def contains_ixscan(value: object) -> bool:
        if isinstance(value, dict):
            return value.get("stage") == "IXSCAN" or any(
                contains_ixscan(item) for item in value.values()
            )
        if isinstance(value, list):
            return any(contains_ixscan(item) for item in value)
        return False

    assert contains_ixscan(plan["queryPlanner"]["winningPlan"])


def test_classroom_seat_history_batch_and_alert_mongo_contract(
    mongodb_database: MongoDatabase,
) -> None:
    initialize_indexes(mongodb_database, [MongoClassroomRepository.ensure_indexes])
    current_time = datetime.now(UTC)
    now = current_time.replace(microsecond=(current_time.microsecond // 1000) * 1000)
    suffix = str(uuid4())
    classroom_operation = f"classroom-create-{suffix}"
    classroom = Classroom(
        id=f"classroom-{suffix}",
        code=f"ROOM-{suffix}",
        name="Integration Classroom",
        location="Building A",
        timezone="Asia/Seoul",
        schedules=(ClassroomSchedule(2, datetime.min.time(), datetime.max.time()),),
        after_hours_grace_minutes=10,
        is_active=True,
        created_at=now,
        updated_at=now,
        version=0,
        created_operation_id=classroom_operation,
        last_operation_id=classroom_operation,
        operation_ids=(classroom_operation,),
    )
    seat_operation = f"seat-create-{suffix}"
    seat = Seat(
        id=f"seat-{suffix}",
        classroom_id=classroom.id,
        code="A-1",
        label="Integration Seat",
        geometry=None,
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
        created_operation_id=seat_operation,
        last_operation_id=seat_operation,
        operation_ids=(seat_operation,),
    )
    event_id = f"seat-event-{suffix}"
    batch = SeatObservationBatchRecord(
        event_id=event_id,
        classroom_id=classroom.id,
        actor_user_id="admin-id",
        observed_at=now,
        observations=(SeatObservation(seat.id, True, 0.9),),
        status=ObservationBatchStatus.PROCESSING,
        processed_count=0,
        changed_count=0,
        alert_count=0,
        received_at=now,
        completed_at=None,
    )
    history = SeatOccupancyHistory(
        id=f"seat-history-{suffix}",
        seat_id=seat.id,
        classroom_id=classroom.id,
        event_id=event_id,
        from_state=SeatOccupancy.UNKNOWN,
        to_state=SeatOccupancy.OCCUPIED,
        occupied=True,
        confidence=0.9,
        observed_at=now,
        received_at=now,
        applied_to_current=True,
        state_changed=True,
    )
    alert_operation = f"after-hours-alert-{suffix}"
    alert = AfterHoursAlert(
        id=f"after-hours-alert-{suffix}",
        dedupe_key=f"{classroom.id}:{seat.id}:{now.date()}:after_hours",
        classroom_id=classroom.id,
        seat_id=seat.id,
        business_date=now.date(),
        status=AfterHoursAlertStatus.OPEN,
        detected_at=now,
        resolved_at=None,
        resolved_by_user_id=None,
        created_operation_id=alert_operation,
        last_operation_id=alert_operation,
        operation_ids=(alert_operation,),
        version=0,
    )
    repository = MongoClassroomRepository(mongodb_database)
    try:
        assert repository.create_classroom(classroom) == classroom
        assert repository.create_classroom(classroom) == classroom
        assert repository.create_seat(seat) == seat
        assert repository.create_seat(seat) == seat
        assert repository.claim_observation_batch(batch) == batch
        assert repository.claim_observation_batch(batch) == batch
        assert repository.append_occupancy_history(history) == history
        assert repository.append_occupancy_history(history) == history

        observation_operation = f"seat-observation:{event_id}:{seat.id}"
        occupied = replace(
            seat,
            current_occupancy=SeatCurrentOccupancy(
                SeatOccupancy.OCCUPIED,
                OccupancySource.MOCK,
                0.9,
                now,
                event_id,
            ),
            version=1,
            last_operation_id=observation_operation,
            operation_ids=(*seat.operation_ids, observation_operation),
        )
        assert repository.replace_seat(occupied, expected_version=0) == occupied
        completed = replace(
            batch,
            status=ObservationBatchStatus.COMPLETED,
            processed_count=1,
            changed_count=1,
            alert_count=1,
            completed_at=now,
        )
        assert repository.complete_observation_batch(completed) == completed
        assert repository.create_alert(alert) == (alert, True)
        assert repository.create_alert(alert) == (alert, False)
        assert (
            repository.list_occupancy_history(
                classroom.id,
                seat_id=seat.id,
                from_time=None,
                to_time=None,
                limit=50,
                offset=0,
            ).total
            == 1
        )
        assert (
            repository.list_alerts(
                status=AfterHoursAlertStatus.OPEN,
                classroom_id=classroom.id,
                business_date=alert.business_date,
                limit=50,
                offset=0,
            ).total
            == 1
        )
    finally:
        mongodb_database["after_hours_alerts"].delete_one({"_id": alert.id})
        mongodb_database["seat_occupancy_history"].delete_many({"classroom_id": classroom.id})
        mongodb_database["seat_observation_batches"].delete_one({"_id": event_id})
        mongodb_database["seats"].delete_many({"classroom_id": classroom.id})
        mongodb_database["classrooms"].delete_one({"_id": classroom.id})


def test_interview_wait_mongo_adapter_contract(mongodb_database: MongoDatabase) -> None:
    initialize_indexes(mongodb_database, [MongoInterviewWaitRepository.ensure_indexes])
    current_time = datetime.now(UTC)
    now = current_time.replace(microsecond=(current_time.microsecond // 1000) * 1000)
    suffix = str(uuid4())
    create_operation = f"interview-wait-create-{suffix}"
    wait = InterviewWait(
        id=f"interview-wait-{suffix}",
        requester_user_id=f"requester-{suffix}",
        employee_id=f"employee-{suffix}",
        status=InterviewWaitStatus.WAITING,
        message="integration interview wait",
        requested_at=now,
        ready_at=None,
        completed_at=None,
        cancelled_at=None,
        expires_at=now + timedelta(hours=24),
        version=0,
        active_key=f"requester-{suffix}:employee-{suffix}",
        created_operation_id=create_operation,
        last_operation_id=create_operation,
        operation_ids=(create_operation,),
        last_actor_user_id=f"requester-{suffix}",
    )
    initial_history = InterviewWaitHistory(
        id=f"interview-wait-history-create-{suffix}",
        wait_id=wait.id,
        from_status=None,
        to_status=InterviewWaitStatus.WAITING,
        reason="integration create",
        actor_user_id=wait.requester_user_id,
        operation_id=create_operation,
        occurred_at=now,
    )
    repository = MongoInterviewWaitRepository(mongodb_database)
    try:
        assert repository.create_wait(wait, initial_history) == wait
        assert repository.create_wait(wait, initial_history) == wait
        assert repository.get_active_wait(wait.requester_user_id, wait.employee_id) == wait
        assert repository.list_history(wait.id) == [initial_history]

        ready_operation = f"interview-wait-ready-{suffix}"
        ready = replace(
            wait,
            status=InterviewWaitStatus.READY,
            ready_at=now,
            version=1,
            last_operation_id=ready_operation,
            operation_ids=(*wait.operation_ids, ready_operation),
        )
        ready_history = InterviewWaitHistory(
            id=f"interview-wait-history-ready-{suffix}",
            wait_id=wait.id,
            from_status=InterviewWaitStatus.WAITING,
            to_status=InterviewWaitStatus.READY,
            reason="integration ready",
            actor_user_id="admin-id",
            operation_id=ready_operation,
            occurred_at=now,
        )
        assert repository.replace_wait(ready, expected_version=0, history=ready_history) == ready
        assert repository.replace_wait(ready, expected_version=0, history=ready_history) == ready
        assert repository.list_history(wait.id) == [initial_history, ready_history]
    finally:
        mongodb_database["interview_wait_history"].delete_many({"wait_id": wait.id})
        mongodb_database["interview_waits"].delete_one({"_id": wait.id})


def test_알림과_mock_delivery_Mongo_adapter_계약(mongodb_database: MongoDatabase) -> None:
    initialize_indexes(mongodb_database, [MongoNotificationRepository.ensure_indexes])
    current_time = datetime.now(UTC)
    now = current_time.replace(microsecond=(current_time.microsecond // 1000) * 1000)
    suffix = str(uuid4())
    notification = Notification(
        id=f"notification-{suffix}",
        recipient_user_id=f"user-{suffix}",
        type="INTEGRATION_TEST",
        title="통합 테스트 알림",
        body="외부 발송 없는 인앱 알림",
        data={"target_route": "/notifications"},
        is_read=False,
        read_at=None,
        dedupe_key=f"integration:{suffix}",
        created_at=now,
        created_operation_id=f"notification-create-{suffix}",
    )
    delivery = MockDelivery(
        id=f"delivery-{suffix}",
        notification_id=notification.id,
        provider="mock",
        status=MockDeliveryStatus.SUCCESS,
        attempt=1,
        operation_id=f"delivery-attempt-{suffix}",
        request_payload={"notification_id": notification.id},
        result_payload={"outcome": "accepted"},
        error=None,
        attempted_at=now,
    )
    repository = MongoNotificationRepository(mongodb_database)
    try:
        assert repository.create_notification(notification) == notification
        assert repository.create_notification(notification) == notification
        assert repository.count_unread(notification.recipient_user_id) == 1
        assert repository.append_delivery(delivery) == delivery
        assert repository.append_delivery(delivery) == delivery
        assert repository.list_notification_deliveries(notification.id) == [delivery]

        marked = repository.mark_read(
            notification.id,
            recipient_user_id=notification.recipient_user_id,
            read_at=now,
            operation_id=f"notification-read-{suffix}",
        )
        assert marked is not None and marked.is_read
        assert repository.count_unread(notification.recipient_user_id) == 0
    finally:
        mongodb_database["notification_deliveries"].delete_many(
            {"notification_id": notification.id}
        )
        mongodb_database["notifications"].delete_one({"_id": notification.id})


def test_사용자_refresh_audit_Mongo_adapter_계약(mongodb_database: MongoDatabase) -> None:
    initialize_indexes(
        mongodb_database,
        [
            MongoUserRepository.ensure_indexes,
            MongoAuthRepository.ensure_indexes,
            MongoAuditRepository.ensure_indexes,
        ],
    )
    current_time = datetime.now(UTC)
    now = current_time.replace(microsecond=(current_time.microsecond // 1000) * 1000)
    suffix = str(uuid4())
    user = User(
        id=f"user-{suffix}",
        email=f"virtual-{suffix}@example.invalid",
        password_hash="$argon2id$integration-redacted",
        name="통합 테스트 가상 사용자",
        role=UserRole.STAFF,
        status=UserStatus.ACTIVE,
        failed_login_count=0,
        locked_until=None,
        last_login_at=None,
        created_at=now,
        updated_at=now,
        version=0,
        created_operation_id=f"create-{suffix}",
        last_operation_id=f"create-{suffix}",
    )
    current_refresh = RefreshToken(
        id=f"refresh-current-{suffix}",
        token_hash=f"hash-current-{suffix}",
        user_id=user.id,
        family_id=f"family-{suffix}",
        expires_at=now + timedelta(hours=1),
        created_at=now,
    )
    replacement = replace(
        current_refresh,
        id=f"refresh-next-{suffix}",
        token_hash=f"hash-next-{suffix}",
    )
    audit_log = AuditLog(
        id=f"audit-{suffix}",
        operation_id=f"audit-operation-{suffix}",
        actor_user_id=user.id,
        action="USER_UPDATED",
        resource_type="user",
        resource_id=user.id,
        before={"role": "STAFF"},
        after={"role": "ADMIN"},
        ip_fingerprint="integration-hmac-fingerprint",
        occurred_at=now,
    )
    users = MongoUserRepository(mongodb_database)
    auth = MongoAuthRepository(mongodb_database)
    audit = MongoAuditRepository(mongodb_database)
    try:
        assert users.create_user(user) == user
        changed = replace(
            user,
            name="수정된 통합 테스트 사용자",
            version=1,
            last_operation_id=f"update-{suffix}",
        )
        assert users.replace_user(changed, expected_version=0) == changed
        assert users.replace_user(changed, expected_version=0) is None

        assert auth.create_refresh_token(current_refresh) == current_refresh
        rotation = auth.rotate_refresh_token(
            current_token_hash=current_refresh.token_hash,
            replacement=replacement,
            now=now,
        )
        assert rotation.status == RefreshRotationStatus.ROTATED
        reused = auth.rotate_refresh_token(
            current_token_hash=current_refresh.token_hash,
            replacement=replace(
                replacement,
                id=f"refresh-unused-{suffix}",
                token_hash=f"hash-unused-{suffix}",
            ),
            now=now,
        )
        assert reused.status == RefreshRotationStatus.REUSED

        assert audit.append(audit_log) == audit_log
        assert audit.get_by_operation_id(audit_log.operation_id) == audit_log
    finally:
        mongodb_database["audit_logs"].delete_one({"_id": audit_log.id})
        mongodb_database["refresh_tokens"].delete_many({"family_id": current_refresh.family_id})
        mongodb_database["users"].delete_one({"_id": user.id})


def test_직원_상태_이력_관측_Mongo_adapter_계약(mongodb_database: MongoDatabase) -> None:
    initialize_indexes(mongodb_database, [MongoEmployeeRepository.ensure_indexes])
    current_time = datetime.now(UTC)
    now = current_time.replace(microsecond=(current_time.microsecond // 1000) * 1000)
    suffix = str(uuid4())
    operation_id = f"employee-create-{suffix}"
    employee = Employee(
        id=f"employee-{suffix}",
        employee_no=f"EMP-{suffix}",
        user_id=None,
        display_name="통합 테스트 직원",
        department="플랫폼팀",
        position="연구원",
        office_zone="A-101",
        is_active=True,
        current_status=EmployeeCurrentStatus(
            status=EmployeeStatus.AWAY,
            source=StatusSource.SYSTEM,
            reason="직원 프로필 생성",
            effective_at=now,
            last_person_seen_at=None,
        ),
        active_override=None,
        created_at=now,
        updated_at=now,
        version=0,
        created_operation_id=operation_id,
        last_operation_id=operation_id,
        operation_ids=(operation_id,),
    )
    initial_history = EmployeeStatusHistory(
        id=f"history-create-{suffix}",
        employee_id=employee.id,
        from_status=None,
        to_status=EmployeeStatus.AWAY,
        source=StatusSource.SYSTEM,
        reason="직원 프로필 생성",
        actor_user_id="admin-id",
        operation_id=operation_id,
        occurred_at=now,
    )
    repository = MongoEmployeeRepository(mongodb_database)
    try:
        assert repository.create_employee(employee, initial_history) == employee
        assert repository.create_employee(employee, initial_history) == employee
        assert repository.get_employee_by_number(employee.employee_no) == employee
        assert (
            repository.list_status_history(
                employee.id,
                limit=50,
                offset=0,
                source=None,
                from_status=None,
                to_status=None,
            ).total
            == 1
        )

        observation = EmployeeObservation(
            event_id=f"observation-{suffix}",
            employee_id=employee.id,
            person_present=True,
            phone_detected=False,
            confidence=0.9,
            observed_at=now,
            received_at=now,
            resulting_status=EmployeeStatus.WORKING,
            status_changed=True,
        )
        assert repository.create_observation(observation) == observation
        assert repository.create_observation(observation) == observation
        assert repository.get_latest_present_observation(employee.id) == observation

        update_operation_id = f"employee-update-{suffix}"
        updated = replace(
            employee,
            display_name="수정된 통합 테스트 직원",
            version=1,
            last_operation_id=update_operation_id,
            operation_ids=(*employee.operation_ids, update_operation_id),
        )
        assert repository.replace_employee(updated, expected_version=0, history=None) == updated
        assert repository.replace_employee(updated, expected_version=0, history=None) == updated
    finally:
        mongodb_database["employee_observations"].delete_many({"employee_id": employee.id})
        mongodb_database["employee_status_history"].delete_many({"employee_id": employee.id})
        mongodb_database["employees"].delete_one({"_id": employee.id})
