"""면담 대기 API와 Jinja2 화면의 HTTP 경계."""

from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from ..auth.dependencies import (
    CSRF_COOKIE,
    get_current_page_user,
    get_current_user,
    require_admin,
    require_csrf,
)
from ..shared.config import Settings
from ..shared.dependencies import get_interview_wait_service, get_settings
from ..shared.errors import DomainError
from ..shared.templating import templates
from ..users.models import ADMIN_ROLES, User
from .models import (
    CreateInterviewWaitCommand,
    EvaluateInterviewWaitExpirationsCommand,
    InterviewWaitStatus,
    TransitionInterviewWaitCommand,
)
from .schemas import (
    CreateInterviewWaitForm,
    CreateInterviewWaitRequest,
    EvaluateInterviewWaitExpirationsRequest,
    InterviewWaitExpirationResponse,
    InterviewWaitListResponse,
    InterviewWaitResponse,
    TransitionInterviewWaitForm,
    UpdateInterviewWaitRequest,
)
from .service import InterviewWaitService

api_router = APIRouter(prefix="/api/v1/interview-waits", tags=["interview-waits"])
expiration_api_router = APIRouter(
    prefix="/api/v1/interview-wait-expirations", tags=["interview-waits"]
)
my_page_router = APIRouter(prefix="/my/interview-waits", tags=["interview-wait-pages"])
staff_page_router = APIRouter(prefix="/staff/interview-waits", tags=["interview-wait-pages"])


def _resolve_paging(limit: int | None, offset: int, settings: Settings) -> tuple[int, int]:
    return min(limit or settings.page_size_default, settings.page_size_max), offset


@api_router.get("", response_model=InterviewWaitListResponse)
def list_interview_waits(
    wait_status: InterviewWaitStatus | None = Query(default=None, alias="status"),
    employee_id: str | None = None,
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    actor: User = Depends(get_current_user),
    service: InterviewWaitService = Depends(get_interview_wait_service),
    settings: Settings = Depends(get_settings),
) -> InterviewWaitListResponse:
    resolved_limit, resolved_offset = _resolve_paging(limit, offset, settings)
    page = service.list_waits(
        actor,
        status=wait_status,
        employee_id=employee_id,
        limit=resolved_limit,
        offset=resolved_offset,
    )
    return InterviewWaitListResponse(
        items=[InterviewWaitResponse.from_wait(item) for item in page.items],
        total=page.total,
        limit=resolved_limit,
        offset=resolved_offset,
    )


@api_router.post(
    "",
    response_model=InterviewWaitResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_interview_wait(
    payload: CreateInterviewWaitRequest,
    response: Response,
    _: None = Depends(require_csrf),
    actor: User = Depends(get_current_user),
    service: InterviewWaitService = Depends(get_interview_wait_service),
) -> InterviewWaitResponse:
    wait = service.create_wait(
        actor,
        CreateInterviewWaitCommand(
            employee_id=payload.employee_id,
            message=payload.message,
            operation_id=str(payload.operation_id),
        ),
    )
    response.headers["Location"] = f"/api/v1/interview-waits/{wait.id}"
    return InterviewWaitResponse.from_wait(wait)


@api_router.get("/{wait_id}", response_model=InterviewWaitResponse)
def get_interview_wait(
    wait_id: str,
    actor: User = Depends(get_current_user),
    service: InterviewWaitService = Depends(get_interview_wait_service),
) -> InterviewWaitResponse:
    return InterviewWaitResponse.from_wait(service.get_wait(actor, wait_id))


@api_router.patch("/{wait_id}", response_model=InterviewWaitResponse)
def update_interview_wait(
    wait_id: str,
    payload: UpdateInterviewWaitRequest,
    _: None = Depends(require_csrf),
    actor: User = Depends(get_current_user),
    service: InterviewWaitService = Depends(get_interview_wait_service),
) -> InterviewWaitResponse:
    wait = service.transition_wait(
        actor,
        TransitionInterviewWaitCommand(
            wait_id=wait_id,
            status=InterviewWaitStatus(payload.status),
            operation_id=str(payload.operation_id),
        ),
    )
    return InterviewWaitResponse.from_wait(wait)


@expiration_api_router.post("", response_model=InterviewWaitExpirationResponse)
def evaluate_interview_wait_expirations(
    payload: EvaluateInterviewWaitExpirationsRequest,
    _: None = Depends(require_csrf),
    actor: User = Depends(require_admin),
    service: InterviewWaitService = Depends(get_interview_wait_service),
) -> InterviewWaitExpirationResponse:
    result = service.evaluate_expirations(
        actor,
        EvaluateInterviewWaitExpirationsCommand(operation_id=str(payload.operation_id)),
    )
    return InterviewWaitExpirationResponse.from_result(result)


@my_page_router.get("")
def my_interview_waits_page(
    request: Request,
    wait_status: InterviewWaitStatus | None = Query(default=None, alias="status"),
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    actor: User = Depends(get_current_page_user),
    service: InterviewWaitService = Depends(get_interview_wait_service),
    settings: Settings = Depends(get_settings),
) -> Response:
    return _render_my_waits(
        request,
        actor=actor,
        service=service,
        settings=settings,
        wait_status=wait_status,
        limit=limit,
        offset=offset,
    )


@my_page_router.post("")
def create_interview_wait_page(
    request: Request,
    form: Annotated[CreateInterviewWaitForm, Form()],
    _: None = Depends(require_csrf),
    actor: User = Depends(get_current_page_user),
    service: InterviewWaitService = Depends(get_interview_wait_service),
    settings: Settings = Depends(get_settings),
) -> Response:
    try:
        wait = service.create_wait(
            actor,
            CreateInterviewWaitCommand(
                employee_id=form.employee_id,
                message=form.message,
                operation_id=str(form.operation_id),
            ),
        )
    except DomainError as exc:
        return _render_my_waits(
            request,
            actor=actor,
            service=service,
            settings=settings,
            error=exc.message,
            status_code=exc.status_code,
        )
    return RedirectResponse(
        url=f"/my/interview-waits/{wait.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@my_page_router.get("/{wait_id}")
def interview_wait_detail_page(
    request: Request,
    wait_id: str,
    actor: User = Depends(get_current_page_user),
    service: InterviewWaitService = Depends(get_interview_wait_service),
) -> Response:
    return _render_wait_detail(request, actor=actor, service=service, wait_id=wait_id)


@my_page_router.post("/{wait_id}/transition")
def transition_interview_wait_page(
    request: Request,
    wait_id: str,
    form: Annotated[TransitionInterviewWaitForm, Form()],
    _: None = Depends(require_csrf),
    actor: User = Depends(get_current_page_user),
    service: InterviewWaitService = Depends(get_interview_wait_service),
) -> Response:
    try:
        service.transition_wait(
            actor,
            TransitionInterviewWaitCommand(
                wait_id=wait_id,
                status=InterviewWaitStatus(form.status),
                operation_id=str(form.operation_id),
            ),
        )
    except DomainError as exc:
        return _render_wait_detail(
            request,
            actor=actor,
            service=service,
            wait_id=wait_id,
            error=exc.message,
            status_code=exc.status_code,
        )
    return RedirectResponse(
        url=f"/my/interview-waits/{wait_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@staff_page_router.get("")
def staff_interview_waits_page(
    request: Request,
    wait_status: InterviewWaitStatus | None = Query(default=None, alias="status"),
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    actor: User = Depends(get_current_page_user),
    service: InterviewWaitService = Depends(get_interview_wait_service),
    settings: Settings = Depends(get_settings),
) -> Response:
    return _render_staff_waits(
        request,
        actor=actor,
        service=service,
        settings=settings,
        wait_status=wait_status,
        limit=limit,
        offset=offset,
    )


@staff_page_router.post("/{wait_id}/complete")
def complete_staff_interview_wait_page(
    request: Request,
    wait_id: str,
    operation_id: str = Form(...),
    _: None = Depends(require_csrf),
    actor: User = Depends(get_current_page_user),
    service: InterviewWaitService = Depends(get_interview_wait_service),
    settings: Settings = Depends(get_settings),
) -> Response:
    try:
        service.transition_wait(
            actor,
            TransitionInterviewWaitCommand(
                wait_id=wait_id,
                status=InterviewWaitStatus.COMPLETED,
                operation_id=operation_id,
            ),
        )
    except DomainError as exc:
        return _render_staff_waits(
            request,
            actor=actor,
            service=service,
            settings=settings,
            error=exc.message,
            status_code=exc.status_code,
        )
    return RedirectResponse(url="/staff/interview-waits", status_code=status.HTTP_303_SEE_OTHER)


def _render_my_waits(
    request: Request,
    *,
    actor: User,
    service: InterviewWaitService,
    settings: Settings,
    wait_status: InterviewWaitStatus | None = None,
    limit: int | None = None,
    offset: int = 0,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    resolved_limit, resolved_offset = _resolve_paging(limit, offset, settings)
    page = service.list_requester_waits(
        actor,
        status=wait_status,
        limit=resolved_limit,
        offset=resolved_offset,
    )
    displays = [service.display(item) for item in page.items]
    return templates.TemplateResponse(
        request=request,
        name="interview_waits/my_list.html",
        context=_page_context(
            request,
            actor,
            page=page,
            displays=displays,
            statuses=list(InterviewWaitStatus),
            selected_status=wait_status,
            cancel_operation_ids={item.id: str(uuid4()) for item in page.items},
            complete_operation_ids={item.id: str(uuid4()) for item in page.items},
            can_cancel={item.id: service.can_cancel(actor, item) for item in page.items},
            can_complete={item.id: service.can_complete(actor, item) for item in page.items},
            limit=resolved_limit,
            offset=resolved_offset,
            has_prev=resolved_offset > 0,
            has_next=resolved_offset + resolved_limit < page.total,
            error=error,
        ),
        status_code=status_code,
    )


def _render_staff_waits(
    request: Request,
    *,
    actor: User,
    service: InterviewWaitService,
    settings: Settings,
    wait_status: InterviewWaitStatus | None = None,
    limit: int | None = None,
    offset: int = 0,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    resolved_limit, resolved_offset = _resolve_paging(limit, offset, settings)
    page = service.list_staff_waits(
        actor,
        status=wait_status,
        limit=resolved_limit,
        offset=resolved_offset,
    )
    displays = [service.display(item) for item in page.items]
    return templates.TemplateResponse(
        request=request,
        name="interview_waits/staff_list.html",
        context=_page_context(
            request,
            actor,
            page=page,
            displays=displays,
            statuses=list(InterviewWaitStatus),
            selected_status=wait_status,
            complete_operation_ids={item.id: str(uuid4()) for item in page.items},
            can_complete={item.id: service.can_complete(actor, item) for item in page.items},
            limit=resolved_limit,
            offset=resolved_offset,
            has_prev=resolved_offset > 0,
            has_next=resolved_offset + resolved_limit < page.total,
            error=error,
        ),
        status_code=status_code,
    )


def _render_wait_detail(
    request: Request,
    *,
    actor: User,
    service: InterviewWaitService,
    wait_id: str,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    wait = service.get_wait(actor, wait_id)
    return templates.TemplateResponse(
        request=request,
        name="interview_waits/detail.html",
        context=_page_context(
            request,
            actor,
            display=service.display(wait),
            history=service.list_history(actor, wait_id),
            can_cancel=service.can_cancel(actor, wait),
            can_complete=service.can_complete(actor, wait),
            cancel_operation_id=str(uuid4()),
            complete_operation_id=str(uuid4()),
            error=error,
        ),
        status_code=status_code,
    )


def _page_context(request: Request, actor: User, **values: object) -> dict[str, object]:
    return {
        "current_user": actor,
        "csrf_token": request.cookies.get(CSRF_COOKIE, ""),
        "can_view_employees": True,
        "can_manage_users": actor.role in ADMIN_ROLES,
        "can_manage_employees": actor.role in ADMIN_ROLES,
        "show_employee_dev_tools": False,
        "show_notification_dev_tools": False,
        **values,
    }
