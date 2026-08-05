"""사용자 관리 API와 Jinja2 화면 진입점."""

from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from ..auth.dependencies import (
    CSRF_COOKIE,
    request_ip_fingerprint,
    require_admin,
    require_csrf,
    require_page_admin,
)
from ..shared.config import Settings
from ..shared.dependencies import get_settings, get_user_service
from ..shared.errors import DomainError
from ..shared.templating import templates
from .models import CreateUserCommand, UpdateUserCommand, User, UserRole, UserStatus
from .schemas import (
    CreateUserForm,
    CreateUserRequest,
    DeactivateUserForm,
    DeactivateUserRequest,
    UpdateUserForm,
    UpdateUserRequest,
    UserListResponse,
    UserResponse,
)
from .service import UserService

api_router = APIRouter(prefix="/api/v1/users", tags=["users"])
page_router = APIRouter(prefix="/admin/users", tags=["users-pages"])


def _resolve_paging(limit: int | None, offset: int, settings: Settings) -> tuple[int, int]:
    resolved_limit = settings.page_size_default if limit is None else limit
    return max(1, min(resolved_limit, settings.page_size_max)), max(0, offset)


def _create_command(payload: CreateUserRequest | CreateUserForm) -> CreateUserCommand:
    return CreateUserCommand(
        email=payload.email,
        password=payload.password,
        name=payload.name,
        role=payload.role,
        operation_id=str(payload.operation_id),
    )


def _update_command(
    user_id: str,
    payload: UpdateUserRequest | UpdateUserForm,
) -> UpdateUserCommand:
    return UpdateUserCommand(
        user_id=user_id,
        expected_version=payload.expected_version,
        operation_id=str(payload.operation_id),
        email=payload.email,
        name=payload.name,
        role=payload.role,
        status=payload.status,
    )


@api_router.get(
    "",
    response_model=UserListResponse,
    summary="관리자 사용자 목록 조회",
)
def list_users(
    role: UserRole | None = None,
    status_filter: UserStatus | None = Query(default=None, alias="status"),
    search: str | None = Query(default=None, max_length=100),
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    actor: User = Depends(require_admin),
    service: UserService = Depends(get_user_service),
    settings: Settings = Depends(get_settings),
) -> UserListResponse:
    resolved_limit, resolved_offset = _resolve_paging(limit, offset, settings)
    page = service.list_users(
        actor,
        limit=resolved_limit,
        offset=resolved_offset,
        role=role,
        status=status_filter,
        search=search,
    )
    return UserListResponse.from_page(page, limit=resolved_limit, offset=resolved_offset)


@api_router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=UserResponse,
    summary="관리자 사용자 생성",
)
def create_user(
    payload: CreateUserRequest,
    response: Response,
    _: None = Depends(require_csrf),
    actor: User = Depends(require_admin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: UserService = Depends(get_user_service),
) -> UserResponse:
    created = service.create_user(
        actor,
        _create_command(payload),
        ip_fingerprint=ip_fingerprint,
    )
    response.headers["Location"] = f"/api/v1/users/{created.id}"
    return UserResponse.from_user(created)


@api_router.get(
    "/{user_id}",
    response_model=UserResponse,
    summary="관리자 사용자 상세 조회",
)
def get_user(
    user_id: str,
    actor: User = Depends(require_admin),
    service: UserService = Depends(get_user_service),
) -> UserResponse:
    return UserResponse.from_user(service.get_user(actor, user_id))


@api_router.patch(
    "/{user_id}",
    response_model=UserResponse,
    summary="관리자 사용자 허용 필드 수정",
)
def update_user(
    user_id: str,
    payload: UpdateUserRequest,
    _: None = Depends(require_csrf),
    actor: User = Depends(require_admin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: UserService = Depends(get_user_service),
) -> UserResponse:
    updated = service.update_user(
        actor,
        _update_command(user_id, payload),
        ip_fingerprint=ip_fingerprint,
    )
    return UserResponse.from_user(updated)


@api_router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="관리자 사용자 soft deactivate",
)
def deactivate_user(
    user_id: str,
    payload: DeactivateUserRequest,
    _: None = Depends(require_csrf),
    actor: User = Depends(require_admin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: UserService = Depends(get_user_service),
) -> Response:
    service.deactivate_user(
        actor,
        user_id,
        operation_id=str(payload.operation_id),
        ip_fingerprint=ip_fingerprint,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@page_router.get("")
def users_page(
    request: Request,
    role: UserRole | None = None,
    status_filter: UserStatus | None = Query(default=None, alias="status"),
    search: str | None = Query(default=None, max_length=100),
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    actor: User = Depends(require_page_admin),
    service: UserService = Depends(get_user_service),
    settings: Settings = Depends(get_settings),
):
    return _render_users_page(
        request,
        actor=actor,
        service=service,
        settings=settings,
        role=role,
        status_filter=status_filter,
        search=search,
        limit=limit,
        offset=offset,
    )


@page_router.post("")
def create_user_page(
    request: Request,
    form: Annotated[CreateUserForm, Form()],
    _: None = Depends(require_csrf),
    actor: User = Depends(require_page_admin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: UserService = Depends(get_user_service),
    settings: Settings = Depends(get_settings),
):
    try:
        service.create_user(actor, _create_command(form), ip_fingerprint=ip_fingerprint)
    except DomainError as exc:
        return _render_users_page(
            request,
            actor=actor,
            service=service,
            settings=settings,
            error=exc.message,
            status_code=exc.status_code,
        )
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)


@page_router.post("/{user_id}/update")
def update_user_page(
    request: Request,
    user_id: str,
    form: Annotated[UpdateUserForm, Form()],
    _: None = Depends(require_csrf),
    actor: User = Depends(require_page_admin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: UserService = Depends(get_user_service),
    settings: Settings = Depends(get_settings),
):
    try:
        service.update_user(
            actor,
            _update_command(user_id, form),
            ip_fingerprint=ip_fingerprint,
        )
    except DomainError as exc:
        return _render_users_page(
            request,
            actor=actor,
            service=service,
            settings=settings,
            error=exc.message,
            status_code=exc.status_code,
        )
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)


@page_router.post("/{user_id}/deactivate")
def deactivate_user_page(
    request: Request,
    user_id: str,
    form: Annotated[DeactivateUserForm, Form()],
    _: None = Depends(require_csrf),
    actor: User = Depends(require_page_admin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: UserService = Depends(get_user_service),
    settings: Settings = Depends(get_settings),
):
    try:
        service.deactivate_user(
            actor,
            user_id,
            operation_id=str(form.operation_id),
            ip_fingerprint=ip_fingerprint,
        )
    except DomainError as exc:
        return _render_users_page(
            request,
            actor=actor,
            service=service,
            settings=settings,
            error=exc.message,
            status_code=exc.status_code,
        )
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)


def _render_users_page(
    request: Request,
    *,
    actor: User,
    service: UserService,
    settings: Settings,
    role: UserRole | None = None,
    status_filter: UserStatus | None = None,
    search: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
):
    resolved_limit, resolved_offset = _resolve_paging(limit, offset, settings)
    page = service.list_users(
        actor,
        limit=resolved_limit,
        offset=resolved_offset,
        role=role,
        status=status_filter,
        search=search,
    )
    return templates.TemplateResponse(
        request=request,
        name="users/list.html",
        context={
            "current_user": actor,
            "can_manage_users": True,
            "can_view_employees": True,
            "can_manage_employees": True,
            "csrf_token": request.cookies.get(CSRF_COOKIE, ""),
            "page": page,
            "roles": list(UserRole),
            "statuses": list(UserStatus),
            "selected_role": role,
            "selected_status": status_filter,
            "search": search or "",
            "limit": resolved_limit,
            "offset": resolved_offset,
            "has_prev": resolved_offset > 0,
            "has_next": resolved_offset + resolved_limit < page.total,
            "error": error,
            "create_operation_id": str(uuid4()),
            "row_operation_ids": {user.id: str(uuid4()) for user in page.items},
            "row_deactivate_operation_ids": {
                user.id: str(uuid4()) for user in page.items
            },
        },
        status_code=status_code,
    )
