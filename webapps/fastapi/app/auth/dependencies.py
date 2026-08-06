"""API와 page router가 공유하는 인증·권한·요청 방어 dependency."""

from __future__ import annotations

from urllib.parse import urlencode
from uuid import uuid4

from fastapi import Depends, Request

from ..employees.models import EmployeeStatus
from ..employees.service import EmployeeService
from ..notifications.service import NotificationService
from ..shared.config import Settings
from ..shared.dependencies import (
    get_auth_service,
    get_employee_service,
    get_notification_service,
    get_settings,
)
from ..shared.errors import RepositoryUnavailableError
from ..shared.security import fingerprint_ip, verify_csrf_token
from ..users.models import ADMIN_ROLES, User, UserRole
from .errors import (
    AuthenticationRequiredError,
    CsrfValidationError,
    InvalidOriginError,
    PageAuthenticationRequired,
    PagePasswordChangeRequired,
    PasswordChangeRequiredError,
    PermissionDeniedError,
)
from .service import AuthService

ACCESS_COOKIE = "som_access"
REFRESH_COOKIE = "som_refresh"
CSRF_COOKIE = "som_csrf"


def require_origin(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> None:
    if request.headers.get("origin") != settings.web_origin:
        raise InvalidOriginError()


async def require_csrf(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> None:
    require_origin(request, settings)
    cookie_token = request.cookies.get(CSRF_COOKIE)
    provided_token = request.headers.get("x-csrf-token")
    if provided_token is None and request.headers.get("content-type", "").startswith(
        "application/x-www-form-urlencoded"
    ):
        provided_token = str((await request.form()).get("csrf_token", ""))
    if (
        not cookie_token
        or not provided_token
        or cookie_token != provided_token
        or settings.csrf_secret is None
        or not verify_csrf_token(provided_token, settings.csrf_secret)
    ):
        raise CsrfValidationError()


def get_authenticated_user(
    request: Request,
    service: AuthService = Depends(get_auth_service),
) -> User:
    return service.authenticate_access_token(request.cookies.get(ACCESS_COOKIE))


def get_current_user(user: User = Depends(get_authenticated_user)) -> User:
    if user.must_change_password:
        raise PasswordChangeRequiredError()
    return user


def get_current_page_user(
    request: Request,
    service: AuthService = Depends(get_auth_service),
    notification_service: NotificationService = Depends(get_notification_service),
    settings: Settings = Depends(get_settings),
    employee_service: EmployeeService = Depends(get_employee_service),
) -> User:
    try:
        user = service.authenticate_access_token(request.cookies.get(ACCESS_COOKIE))
        if user.must_change_password:
            path = request.url.path
            if request.url.query:
                path = f"{path}?{request.url.query}"
            raise PagePasswordChangeRequired(path)
        _set_page_navigation_state(
            request,
            user=user,
            settings=settings,
            notification_unread_count=_popover_unread_count(
                request, user=user, notification_service=notification_service
            ),
        )
        _set_profile_state(request, user=user, employee_service=employee_service)
        return user
    except AuthenticationRequiredError:
        path = request.url.path
        if request.url.query:
            path = f"{path}?{request.url.query}"
        raise PageAuthenticationRequired(path) from None


def get_password_change_page_user(
    request: Request,
    service: AuthService = Depends(get_auth_service),
    notification_service: NotificationService = Depends(get_notification_service),
    settings: Settings = Depends(get_settings),
    employee_service: EmployeeService = Depends(get_employee_service),
) -> User:
    try:
        user = service.authenticate_access_token(request.cookies.get(ACCESS_COOKIE))
    except AuthenticationRequiredError:
        raise PageAuthenticationRequired(request.url.path) from None
    _set_page_navigation_state(
        request,
        user=user,
        settings=settings,
        notification_unread_count=_popover_unread_count(
            request, user=user, notification_service=notification_service
        ),
    )
    _set_profile_state(request, user=user, employee_service=employee_service)
    return user


def get_optional_page_user(
    request: Request,
    service: AuthService = Depends(get_auth_service),
    notification_service: NotificationService = Depends(get_notification_service),
    settings: Settings = Depends(get_settings),
    employee_service: EmployeeService = Depends(get_employee_service),
) -> User | None:
    try:
        user = service.authenticate_access_token(request.cookies.get(ACCESS_COOKIE))
        _set_page_navigation_state(
            request,
            user=user,
            settings=settings,
            notification_unread_count=_popover_unread_count(
                request, user=user, notification_service=notification_service
            ),
        )
        _set_profile_state(request, user=user, employee_service=employee_service)
        return user
    except AuthenticationRequiredError:
        _set_page_navigation_state(
            request,
            user=None,
            settings=settings,
            notification_unread_count=0,
        )
        _set_profile_state(request, user=None, employee_service=employee_service)
        return None


def _set_profile_state(
    request: Request,
    *,
    user: User | None,
    employee_service: EmployeeService,
) -> None:
    request.state.profile_employee = (
        None if user is None else employee_service.get_linked_employee(user)
    )
    request.state.profile_set_operation_id = str(uuid4())
    request.state.profile_clear_operation_id = str(uuid4())
    request.state.profile_statuses = list(EmployeeStatus)


def _popover_unread_count(
    request: Request,
    *,
    user: User,
    notification_service: NotificationService,
) -> int:
    try:
        request.state.notification_load_failed = False
        return notification_service.popover_unread_count(user)
    except RepositoryUnavailableError:
        request.state.notification_load_failed = True
        return 0


def _set_page_navigation_state(
    request: Request,
    *,
    user: User | None,
    settings: Settings,
    notification_unread_count: int,
) -> None:
    """모든 Jinja 화면이 같은 역할·환경 기반 탐색 상태를 사용하게 한다."""
    is_admin = user is not None and user.role in ADMIN_ROLES
    request.state.is_student = user is not None and user.role == UserRole.STUDENT
    request.state.is_staff = user is not None and user.role == UserRole.STAFF
    request.state.is_admin = is_admin
    request.state.notification_unread_count = notification_unread_count
    request.state.can_view_staff_waits = user is not None and user.role == UserRole.STAFF
    request.state.show_employee_dev_tools = settings.mock_inputs_enabled and is_admin
    request.state.show_notification_dev_tools = settings.mock_inputs_enabled and is_admin


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role not in ADMIN_ROLES:
        raise PermissionDeniedError()
    return user


def require_page_admin(user: User = Depends(get_current_page_user)) -> User:
    if user.role not in ADMIN_ROLES:
        raise PermissionDeniedError()
    return user


def can_manage_users(user: User | None) -> bool:
    return user is not None and user.role in ADMIN_ROLES


def login_redirect(return_to: str) -> str:
    return "/login?" + urlencode({"next": return_to})


def product_home_path(role: UserRole) -> str:
    """제품 역할별 로그인 홈. 레거시 운영자 데이터는 관리자 홈으로 격리한다."""
    if role == UserRole.STUDENT:
        return "/employees"
    if role == UserRole.STAFF:
        return "/staff/interview-waits"
    return "/admin"


def request_ip_fingerprint(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> str:
    address = request.client.host if request.client is not None else "unknown"
    assert settings.audit_ip_hash_secret is not None
    return fingerprint_ip(address, settings.audit_ip_hash_secret)
