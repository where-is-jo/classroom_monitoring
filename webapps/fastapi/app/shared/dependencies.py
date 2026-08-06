"""의존성 조립 지점.

**어댑터를 서비스에 연결하는 곳은 여기 한 곳뿐이다.**
주입은 FastAPI `Depends`를 쓴다 (ADR-0002 후속 결정). 별도 DI 컨테이너를 두지 않는다.

저장소를 MongoDB로 바꿀 때 고치는 파일도 여기다.
`get_event_repository`가 반환하는 구현체만 교체하면 서비스와 라우터는 그대로다.

조립을 한곳에 모은 이 파일이 흔히 Composition Root라 부르는 것에 해당한다.
어휘 대응표는 ADR-0005에 있다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache

from fastapi import Depends
from pymongo import MongoClient

from ..admin.adapters.memory_repository import InMemoryAdminDashboardRepository
from ..admin.adapters.mongo_repository import MongoAdminDashboardRepository
from ..admin.ports import AdminDashboardRepository
from ..admin.service import AdminDashboardService
from ..audit.adapters.memory_repository import InMemoryAuditRepository
from ..audit.adapters.mongo_repository import MongoAuditRepository
from ..audit.ports import AuditRepository
from ..audit.service import AuditService
from ..auth.adapters.memory_repository import InMemoryAuthRepository
from ..auth.adapters.mongo_repository import MongoAuthRepository
from ..auth.ports import AuthRepository
from ..auth.service import AuthService, LoginRateLimiter
from ..classrooms.adapters.memory_repository import InMemoryClassroomRepository
from ..classrooms.adapters.mongo_repository import MongoClassroomRepository
from ..classrooms.ports import ClassroomRepository
from ..classrooms.service import ClassroomService, ClassroomStaffAssignmentService
from ..employees.adapters.memory_repository import InMemoryEmployeeRepository
from ..employees.adapters.mongo_repository import MongoEmployeeRepository
from ..employees.ports import EmployeeRepository
from ..employees.service import EmployeeService
from ..events.adapters.memory_repository import InMemoryEventRepository
from ..events.adapters.mongo_repository import MongoEventRepository
from ..events.ports import EventRepository
from ..events.service import EventService
from ..interview_waits.adapters.memory_repository import InMemoryInterviewWaitRepository
from ..interview_waits.adapters.mongo_repository import MongoInterviewWaitRepository
from ..interview_waits.ports import InterviewWaitRepository
from ..interview_waits.service import EmployeeInterviewCoordinator, InterviewWaitService
from ..notifications.adapters.memory_repository import InMemoryNotificationRepository
from ..notifications.adapters.mongo_repository import MongoNotificationRepository
from ..notifications.ports import NotificationRepository
from ..notifications.service import NotificationService
from ..users.adapters.memory_repository import InMemoryUserRepository
from ..users.adapters.mongo_repository import MongoUserRepository
from ..users.ports import UserRepository
from ..users.seed import VirtualSeedPasswords, seed_virtual_users
from ..users.service import UserService
from ..video_monitoring.service import VideoDemoService
from .config import Settings
from .database import (
    DatabaseOperationError,
    MongoDatabase,
    MongoDocument,
    create_mongo_client,
    initialize_indexes,
    ping_database,
    select_database,
)
from .errors import DatabaseUnavailableError
from .security import PasswordSecurity, TokenSecurity


def utc_now() -> datetime:
    return datetime.now(UTC)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


@lru_cache
def _event_repository() -> InMemoryEventRepository:
    return InMemoryEventRepository()


@lru_cache
def _user_repository() -> InMemoryUserRepository:
    return InMemoryUserRepository()


@lru_cache
def _auth_repository() -> InMemoryAuthRepository:
    return InMemoryAuthRepository()


@lru_cache
def _audit_repository() -> InMemoryAuditRepository:
    return InMemoryAuditRepository()


@lru_cache
def _employee_repository() -> InMemoryEmployeeRepository:
    return InMemoryEmployeeRepository()


@lru_cache
def _notification_repository() -> InMemoryNotificationRepository:
    return InMemoryNotificationRepository()


@lru_cache
def _interview_wait_repository() -> InMemoryInterviewWaitRepository:
    return InMemoryInterviewWaitRepository()


@lru_cache
def _classroom_repository() -> InMemoryClassroomRepository:
    return InMemoryClassroomRepository()


@lru_cache
def _admin_dashboard_repository() -> InMemoryAdminDashboardRepository:
    return InMemoryAdminDashboardRepository(
        _employee_repository(),
        _classroom_repository(),
        _audit_repository(),
    )


@lru_cache
def _mongo_client() -> MongoClient[MongoDocument]:
    settings = get_settings()
    if settings.database_url is None:
        raise DatabaseOperationError("DATABASE_URL이 필요합니다.")
    return create_mongo_client(
        settings.database_url,
        timeout_seconds=settings.database_connect_timeout_seconds,
    )


@lru_cache
def _mongo_database() -> MongoDatabase:
    settings = get_settings()
    if settings.database_name is None:
        raise DatabaseOperationError("DATABASE_NAME이 필요합니다.")
    return select_database(_mongo_client(), settings.database_name)


@lru_cache
def _mongo_event_repository() -> MongoEventRepository:
    return MongoEventRepository(_mongo_database())


@lru_cache
def _mongo_user_repository() -> MongoUserRepository:
    return MongoUserRepository(_mongo_database())


@lru_cache
def _mongo_auth_repository() -> MongoAuthRepository:
    return MongoAuthRepository(_mongo_database())


@lru_cache
def _mongo_audit_repository() -> MongoAuditRepository:
    return MongoAuditRepository(_mongo_database())


@lru_cache
def _mongo_employee_repository() -> MongoEmployeeRepository:
    return MongoEmployeeRepository(_mongo_database())


@lru_cache
def _mongo_notification_repository() -> MongoNotificationRepository:
    return MongoNotificationRepository(_mongo_database())


@lru_cache
def _mongo_interview_wait_repository() -> MongoInterviewWaitRepository:
    return MongoInterviewWaitRepository(_mongo_database())


@lru_cache
def _mongo_classroom_repository() -> MongoClassroomRepository:
    return MongoClassroomRepository(_mongo_database())


@lru_cache
def _mongo_admin_dashboard_repository() -> MongoAdminDashboardRepository:
    return MongoAdminDashboardRepository(_mongo_database())


def get_event_repository(settings: Settings = Depends(get_settings)) -> EventRepository:
    if settings.database_mode == "memory":
        return _event_repository()
    return _mongo_event_repository()


def get_user_repository(settings: Settings = Depends(get_settings)) -> UserRepository:
    if settings.database_mode == "memory":
        return _user_repository()
    return _mongo_user_repository()


def get_auth_repository(settings: Settings = Depends(get_settings)) -> AuthRepository:
    if settings.database_mode == "memory":
        return _auth_repository()
    return _mongo_auth_repository()


def get_audit_repository(settings: Settings = Depends(get_settings)) -> AuditRepository:
    if settings.database_mode == "memory":
        return _audit_repository()
    return _mongo_audit_repository()


def get_employee_repository(
    settings: Settings = Depends(get_settings),
) -> EmployeeRepository:
    if settings.database_mode == "memory":
        return _employee_repository()
    return _mongo_employee_repository()


def get_notification_repository(
    settings: Settings = Depends(get_settings),
) -> NotificationRepository:
    if settings.database_mode == "memory":
        return _notification_repository()
    return _mongo_notification_repository()


def get_interview_wait_repository(
    settings: Settings = Depends(get_settings),
) -> InterviewWaitRepository:
    if settings.database_mode == "memory":
        return _interview_wait_repository()
    return _mongo_interview_wait_repository()


def get_classroom_repository(
    settings: Settings = Depends(get_settings),
) -> ClassroomRepository:
    if settings.database_mode == "memory":
        return _classroom_repository()
    return _mongo_classroom_repository()


def get_admin_dashboard_repository(
    settings: Settings = Depends(get_settings),
) -> AdminDashboardRepository:
    if settings.database_mode == "memory":
        return _admin_dashboard_repository()
    return _mongo_admin_dashboard_repository()


@lru_cache
def get_password_security() -> PasswordSecurity:
    return PasswordSecurity()


@lru_cache
def get_token_security() -> TokenSecurity:
    settings = get_settings()
    assert settings.jwt_access_secret is not None
    assert settings.jwt_refresh_secret is not None
    return TokenSecurity(
        access_secret=settings.jwt_access_secret,
        refresh_secret=settings.jwt_refresh_secret,
        access_ttl_seconds=settings.auth_access_token_ttl_seconds,
        refresh_ttl_seconds=settings.auth_refresh_token_ttl_seconds,
    )


@lru_cache
def get_login_rate_limiter() -> LoginRateLimiter:
    settings = get_settings()
    return LoginRateLimiter(
        max_failures=settings.auth_ip_max_failures,
        window_seconds=settings.auth_ip_window_seconds,
    )


def get_audit_service(
    repository: AuditRepository = Depends(get_audit_repository),
) -> AuditService:
    return AuditService(repository, clock=utc_now)


def get_classroom_staff_assignment_service(
    repository: ClassroomRepository = Depends(get_classroom_repository),
    audit_service: AuditService = Depends(get_audit_service),
) -> ClassroomStaffAssignmentService:
    return ClassroomStaffAssignmentService(repository, audit_service, clock=utc_now)


def get_user_service(
    repository: UserRepository = Depends(get_user_repository),
    auth_repository: AuthRepository = Depends(get_auth_repository),
    audit_service: AuditService = Depends(get_audit_service),
    password_security: PasswordSecurity = Depends(get_password_security),
    staff_assignment_policy: ClassroomStaffAssignmentService = Depends(
        get_classroom_staff_assignment_service
    ),
    settings: Settings = Depends(get_settings),
) -> UserService:
    return UserService(
        repository,
        auth_repository,
        audit_service,
        password_security,
        password_min_length=settings.auth_password_min_length,
        clock=utc_now,
        staff_assignment_policy=staff_assignment_policy,
    )


def get_auth_service(
    user_repository: UserRepository = Depends(get_user_repository),
    auth_repository: AuthRepository = Depends(get_auth_repository),
    audit_service: AuditService = Depends(get_audit_service),
    password_security: PasswordSecurity = Depends(get_password_security),
    token_security: TokenSecurity = Depends(get_token_security),
    rate_limiter: LoginRateLimiter = Depends(get_login_rate_limiter),
    settings: Settings = Depends(get_settings),
) -> AuthService:
    return AuthService(
        user_repository,
        auth_repository,
        audit_service,
        password_security,
        token_security,
        rate_limiter,
        account_max_failures=settings.auth_login_max_failures,
        lockout_seconds=settings.auth_lockout_seconds,
        clock=utc_now,
    )


def get_event_service(
    repository: EventRepository = Depends(get_event_repository),
    settings: Settings = Depends(get_settings),
) -> EventService:
    return EventService(
        repository,
        high_confidence_threshold=settings.high_confidence_threshold,
        medium_confidence_threshold=settings.medium_confidence_threshold,
    )


def get_employee_service(
    repository: EmployeeRepository = Depends(get_employee_repository),
    user_repository: UserRepository = Depends(get_user_repository),
    audit_service: AuditService = Depends(get_audit_service),
    settings: Settings = Depends(get_settings),
) -> EmployeeService:
    return EmployeeService(
        repository,
        user_repository,
        audit_service,
        away_after_seconds=settings.employee_away_after_seconds,
        offsite_after_seconds=settings.employee_offsite_after_seconds,
        clock=utc_now,
    )


def get_notification_service(
    repository: NotificationRepository = Depends(get_notification_repository),
    user_repository: UserRepository = Depends(get_user_repository),
    settings: Settings = Depends(get_settings),
) -> NotificationService:
    return NotificationService(
        repository,
        user_repository,
        clock=utc_now,
        mock_delivery_mode=(
            settings.notification_mock_delivery_mode if settings.mock_inputs_enabled else None
        ),
        mock_delivery_max_attempts=(settings.notification_mock_delivery_max_attempts),
    )


def get_interview_wait_service(
    repository: InterviewWaitRepository = Depends(get_interview_wait_repository),
    employee_repository: EmployeeRepository = Depends(get_employee_repository),
    user_repository: UserRepository = Depends(get_user_repository),
    notification_service: NotificationService = Depends(get_notification_service),
    settings: Settings = Depends(get_settings),
) -> InterviewWaitService:
    return InterviewWaitService(
        repository,
        employee_repository,
        user_repository,
        notification_service,
        expires_after_hours=settings.interview_wait_expires_after_hours,
        clock=utc_now,
    )


def get_employee_interview_coordinator(
    employee_service: EmployeeService = Depends(get_employee_service),
    interview_wait_service: InterviewWaitService = Depends(get_interview_wait_service),
) -> EmployeeInterviewCoordinator:
    return EmployeeInterviewCoordinator(employee_service, interview_wait_service)


def get_classroom_service(
    repository: ClassroomRepository = Depends(get_classroom_repository),
    user_repository: UserRepository = Depends(get_user_repository),
    notification_service: NotificationService = Depends(get_notification_service),
    audit_service: AuditService = Depends(get_audit_service),
    settings: Settings = Depends(get_settings),
) -> ClassroomService:
    return ClassroomService(
        repository,
        user_repository,
        notification_service,
        audit_service,
        occupancy_confidence_threshold=(settings.seat_occupancy_confidence_threshold),
        clock=utc_now,
    )


def get_admin_dashboard_service(
    repository: AdminDashboardRepository = Depends(get_admin_dashboard_repository),
) -> AdminDashboardService:
    return AdminDashboardService(repository, clock=utc_now)


def get_video_demo_service() -> VideoDemoService:
    return VideoDemoService(clock=utc_now)


def initialize_data_store() -> None:
    """시작 시 연결·index를 검증하고 opt-in 가상 사용자를 seed한다."""
    settings = get_settings()
    if settings.database_mode == "mongodb":
        database = _mongo_database()
        ping_database(database)
        initialize_indexes(
            database,
            [
                MongoEventRepository.ensure_indexes,
                MongoUserRepository.ensure_indexes,
                MongoAuthRepository.ensure_indexes,
                MongoAuditRepository.ensure_indexes,
                MongoEmployeeRepository.ensure_indexes,
                MongoNotificationRepository.ensure_indexes,
                MongoInterviewWaitRepository.ensure_indexes,
                MongoClassroomRepository.ensure_indexes,
                MongoAdminDashboardRepository.ensure_indexes,
            ],
        )
    if settings.auth_seed_enabled:
        _seed_users(settings)


def _seed_users(settings: Settings) -> None:
    assert settings.auth_seed_student_password is not None
    assert settings.auth_seed_staff_password is not None
    assert settings.auth_seed_admin_password is not None
    assert settings.auth_seed_system_operator_password is not None
    audit_service = get_audit_service(get_audit_repository(settings))
    user_service = get_user_service(
        repository=get_user_repository(settings),
        auth_repository=get_auth_repository(settings),
        audit_service=audit_service,
        password_security=get_password_security(),
        staff_assignment_policy=get_classroom_staff_assignment_service(
            get_classroom_repository(settings), audit_service
        ),
        settings=settings,
    )
    seed_virtual_users(
        user_service,
        VirtualSeedPasswords(
            student=settings.auth_seed_student_password.get_secret_value(),
            staff=settings.auth_seed_staff_password.get_secret_value(),
            admin=settings.auth_seed_admin_password.get_secret_value(),
            system_operator=(settings.auth_seed_system_operator_password.get_secret_value()),
        ),
    )


def close_data_store() -> None:
    if _mongo_client.cache_info().currsize:
        _mongo_client().close()
    _mongo_audit_repository.cache_clear()
    _mongo_employee_repository.cache_clear()
    _mongo_notification_repository.cache_clear()
    _mongo_interview_wait_repository.cache_clear()
    _mongo_classroom_repository.cache_clear()
    _mongo_admin_dashboard_repository.cache_clear()
    _mongo_auth_repository.cache_clear()
    _mongo_user_repository.cache_clear()
    _mongo_event_repository.cache_clear()
    _mongo_database.cache_clear()
    _mongo_client.cache_clear()


def verify_readiness(settings: Settings = Depends(get_settings)) -> None:
    if settings.database_mode == "memory":
        return
    try:
        ping_database(_mongo_database())
    except DatabaseOperationError:
        raise DatabaseUnavailableError() from None
