"""인증 API와 Jinja2 화면 진입점."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import RedirectResponse

from ..shared.config import Settings
from ..shared.dependencies import get_auth_service, get_settings, get_user_service
from ..shared.security import issue_csrf_token
from ..shared.templating import templates
from ..users.models import ChangePasswordCommand, User
from ..users.schemas import UserResponse
from ..users.service import UserService
from .dependencies import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    REFRESH_COOKIE,
    get_current_user,
    request_ip_fingerprint,
    require_csrf,
    require_origin,
)
from .errors import AccountLockedError, InvalidCredentialsError, LoginRateLimitedError
from .models import AuthenticatedSession, LoginCommand
from .schemas import (
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
    _: None = Depends(require_csrf),
    actor: User = Depends(get_current_user),
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
    return MeResponse.from_user(changed)


@page_router.get("/login")
def login_page(request: Request, next: str = "/events") -> Response:
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
    response = RedirectResponse(
        url=_safe_return_to(form.next),
        status_code=status.HTTP_303_SEE_OTHER,
    )
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


def _safe_return_to(value: str) -> str:
    if not value.startswith("/") or value.startswith("//"):
        return "/events"
    return value
