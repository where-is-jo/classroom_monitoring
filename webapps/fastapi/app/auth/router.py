"""인증 API와 Jinja2 화면 진입점."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import RedirectResponse

from ..shared.config import Settings
from ..shared.dependencies import get_auth_service, get_settings, get_user_service
from ..shared.errors import DomainError
from ..shared.security import issue_csrf_token
from ..shared.templating import templates
from ..users.models import ChangePasswordCommand, User, UserRole
from ..users.schemas import UserResponse
from ..users.service import UserService
from .dependencies import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    REFRESH_COOKIE,
    get_authenticated_user,
    get_current_user,
    get_password_change_page_user,
    product_home_path,
    request_ip_fingerprint,
    require_csrf,
    require_origin,
)
from .errors import AccountLockedError, InvalidCredentialsError, LoginRateLimitedError
from .models import AuthenticatedSession, LoginCommand
from .schemas import (
    ChangePasswordForm,
    ChangePasswordRequest,
    LoginForm,
    LoginRequest,
    MeResponse,
    SessionResponse,
)
from .service import AuthService

api_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
page_router = APIRouter(tags=["auth-pages"])


def _session_response(session: AuthenticatedSession) -> SessionResponse:
    return SessionResponse(
        user=UserResponse.from_user(session.user),
        access_expires_at=session.tokens.access_expires_at,
        refresh_expires_at=session.tokens.refresh_expires_at,
    )


def _set_session_cookies(
    response: Response,
    session: AuthenticatedSession,
    *,
    settings: Settings,
) -> str:
    secure = settings.app_env == "prod"
    response.set_cookie(
        ACCESS_COOKIE,
        session.tokens.access_token,
        max_age=settings.auth_access_token_ttl_seconds,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        session.tokens.refresh_token,
        max_age=settings.auth_refresh_token_ttl_seconds,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    assert settings.csrf_secret is not None
    csrf_token = issue_csrf_token(settings.csrf_secret)
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=settings.auth_refresh_token_ttl_seconds,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )
    return csrf_token


def _clear_session_cookies(response: Response) -> None:
    for name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE):
        response.delete_cookie(name, path="/", samesite="lax")


@api_router.post(
    "/login",
    response_model=SessionResponse,
    summary="이메일·비밀번호 로그인과 세션 cookie 발급",
)
def login(
    payload: LoginRequest,
    response: Response,
    _: None = Depends(require_origin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> SessionResponse:
    session = service.login(
        LoginCommand(
            email=payload.email,
            password=payload.password,
            ip_fingerprint=ip_fingerprint,
        )
    )
    _set_session_cookies(response, session, settings=settings)
    return _session_response(session)


@api_router.post(
    "/refresh",
    response_model=SessionResponse,
    summary="refresh token rotation",
)
def refresh(
    request: Request,
    response: Response,
    _: None = Depends(require_csrf),
    service: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> SessionResponse:
    session = service.refresh(request.cookies.get(REFRESH_COOKIE, ""))
    _set_session_cookies(response, session, settings=settings)
    return _session_response(session)


@api_router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="현재 refresh family 폐기와 로그아웃",
)
def logout(
    request: Request,
    response: Response,
    _: None = Depends(require_csrf),
    service: AuthService = Depends(get_auth_service),
) -> Response:
    service.logout(request.cookies.get(REFRESH_COOKIE))
    _clear_session_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@api_router.get("/me", response_model=MeResponse, summary="현재 로그인 사용자 조회")
def me(user: User = Depends(get_current_user)) -> MeResponse:
    return MeResponse.from_user(user)


@api_router.patch(
    "/me/password",
    response_model=MeResponse,
    summary="본인 비밀번호 변경과 refresh token 전체 폐기",
)
def change_password(
    payload: ChangePasswordRequest,
    response: Response,
    _: None = Depends(require_csrf),
    actor: User = Depends(get_authenticated_user),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: UserService = Depends(get_user_service),
) -> MeResponse:
    changed = service.change_password(
        actor,
        ChangePasswordCommand(
            current_password=payload.current_password,
            new_password=payload.new_password,
            operation_id=str(payload.operation_id),
        ),
        ip_fingerprint=ip_fingerprint,
    )
    _clear_session_cookies(response)
    return MeResponse.from_user(changed)


@page_router.get("/login")
def login_page(request: Request, next: str = "") -> Response:
    return templates.TemplateResponse(
        request=request,
        name="auth/login.html",
        context={"next": _safe_return_to(next), "error": None, "locked": False},
    )


@page_router.post("/login")
def login_page_submit(
    request: Request,
    form: Annotated[LoginForm, Form()],
    _: None = Depends(require_origin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> Response:
    try:
        session = service.login(
            LoginCommand(
                email=form.email,
                password=form.password,
                ip_fingerprint=ip_fingerprint,
            )
        )
    except (InvalidCredentialsError, AccountLockedError, LoginRateLimitedError) as exc:
        return templates.TemplateResponse(
            request=request,
            name="auth/login.html",
            context={
                "next": _safe_return_to(form.next),
                "error": exc.message,
                "locked": isinstance(exc, AccountLockedError),
            },
            status_code=exc.status_code,
        )
    destination = _return_to_for(session.user, form.next)
    if session.user.must_change_password:
        destination = "/account/password?" + urlencode({"next": destination})
    response = RedirectResponse(url=destination, status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookies(response, session, settings=settings)
    return response


@page_router.post("/logout")
def logout_page(
    request: Request,
    _: None = Depends(require_csrf),
    service: AuthService = Depends(get_auth_service),
) -> Response:
    service.logout(request.cookies.get(REFRESH_COOKIE))
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    _clear_session_cookies(response)
    return response


@page_router.get("/account/password")
def password_page(
    request: Request,
    next: str = "",
    actor: User = Depends(get_password_change_page_user),
) -> Response:
    return _render_password_page(
        request,
        actor=actor,
        next_path=_return_to_for(actor, next),
    )


@page_router.post("/account/password")
def password_page_submit(
    request: Request,
    form: Annotated[ChangePasswordForm, Form()],
    _: None = Depends(require_csrf),
    actor: User = Depends(get_password_change_page_user),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: UserService = Depends(get_user_service),
) -> Response:
    next_path = _return_to_for(actor, form.next)
    if form.new_password != form.new_password_confirm:
        return _render_password_page(
            request,
            actor=actor,
            next_path=next_path,
            error="새 비밀번호 확인이 일치하지 않습니다.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    try:
        service.change_password(
            actor,
            ChangePasswordCommand(
                current_password=form.current_password,
                new_password=form.new_password,
                operation_id=str(form.operation_id),
            ),
            ip_fingerprint=ip_fingerprint,
        )
    except DomainError as exc:
        return _render_password_page(
            request,
            actor=actor,
            next_path=next_path,
            error=exc.message,
            status_code=exc.status_code,
        )
    response = RedirectResponse(
        url="/login?" + urlencode({"next": next_path}),
        status_code=status.HTTP_303_SEE_OTHER,
    )
    _clear_session_cookies(response)
    return response


def _render_password_page(
    request: Request,
    *,
    actor: User,
    next_path: str,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    return templates.TemplateResponse(
        request=request,
        name="account/password.html",
        context={
            "current_user": actor,
            "csrf_token": request.cookies.get(CSRF_COOKIE, ""),
            "next": next_path,
            "error": error,
            "must_change_password": actor.must_change_password,
        },
        status_code=status_code,
    )


def _safe_return_to(value: str) -> str:
    if not value.startswith("/") or value.startswith("//"):
        return ""
    return value


def _return_to_for(user: User, value: str) -> str:
    candidate = _safe_return_to(value)
    allowed_prefixes = {
        UserRole.STUDENT: ("/employees", "/my/interview-waits", "/classrooms", "/account"),
        UserRole.STAFF: (
            "/employees",
            "/staff/interview-waits",
            "/classrooms",
            "/monitoring",
            "/video-search",
            "/account",
        ),
        UserRole.ADMIN: (
            "/admin",
            "/employees",
            "/classrooms",
            "/monitoring",
            "/video-search",
            "/account",
        ),
        UserRole.SYSTEM_OPERATOR: ("/admin", "/employees", "/classrooms", "/account"),
    }
    if candidate and candidate.startswith(allowed_prefixes[user.role]):
        return candidate
    return product_home_path(user.role)
