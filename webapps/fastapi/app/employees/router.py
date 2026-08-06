"""직원 프로필·상태 API와 Jinja2 화면 진입점."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from ..auth.dependencies import (
    CSRF_COOKIE,
    get_current_page_user,
    get_current_user,
    request_ip_fingerprint,
    require_admin,
    require_csrf,
    require_page_admin,
)
from ..interview_waits.service import EmployeeInterviewCoordinator
from ..shared.config import Settings
from ..shared.dependencies import (
    get_employee_interview_coordinator,
    get_employee_service,
    get_settings,
    get_user_service,
)
from ..shared.errors import DomainError
from ..shared.templating import templates
from ..users.models import ADMIN_ROLES, User, UserRole, UserStatus
from ..users.service import UserService
from .models import (
    ClearStatusOverrideCommand,
    CreateEmployeeCommand,
    EmployeeStatus,
    EvaluateEmployeeStatusesCommand,
    RecordEmployeeObservationCommand,
    SetStatusOverrideCommand,
    StatusSource,
    UpdateEmployeeCommand,
)
from .schemas import (
    ClearStatusOverrideForm,
    ClearStatusOverrideRequest,
    CreateEmployeeForm,
    CreateEmployeeRequest,
    DeactivateEmployeeForm,
    DeactivateEmployeeRequest,
    EmployeeListResponse,
    EmployeeObservationResponse,
    EmployeeResponse,
    EmployeeStatusEvaluationResponse,
    EmployeeStatusHistoryListResponse,
    EvaluateEmployeeStatusesForm,
    EvaluateEmployeeStatusesRequest,
    MockEmployeeObservationForm,
    MockEmployeeObservationRequest,
    SetStatusOverrideForm,
    SetStatusOverrideRequest,
    UpdateEmployeeForm,
    UpdateEmployeeRequest,
)
from .service import EmployeeService

api_router = APIRouter(prefix="/api/v1/employees", tags=["employees"])
evaluation_api_router = APIRouter(
    prefix="/api/v1/employee-status-evaluations",
    tags=["employees"],
)
development_api_router = APIRouter(
    prefix="/api/v1/mock-employee-observations",
    tags=["development"],
)
page_router = APIRouter(prefix="/employees", tags=["employee-pages"])
admin_page_router = APIRouter(
    prefix="/admin/employees", tags=["employee-admin-pages"]
)
development_page_router = APIRouter(
    prefix="/admin/dev-tools", tags=["development-pages"]
)


def _resolve_paging(
    limit: int | None,
    offset: int,
    settings: Settings,
) -> tuple[int, int]:
    resolved_limit = settings.page_size_default if limit is None else limit
    return max(1, min(resolved_limit, settings.page_size_max)), max(0, offset)


def _optional_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _create_command(
    payload: CreateEmployeeRequest | CreateEmployeeForm,
) -> CreateEmployeeCommand:
    return CreateEmployeeCommand(
        employee_no=payload.employee_no,
        user_id=_optional_identifier(payload.user_id),
        display_name=payload.display_name,
        department=payload.department,
        position=payload.position,
        office_zone=payload.office_zone,
        operation_id=str(payload.operation_id),
    )


def _update_command(
    employee_id: str,
    payload: UpdateEmployeeRequest | UpdateEmployeeForm,
) -> UpdateEmployeeCommand:
    return UpdateEmployeeCommand(
        employee_id=employee_id,
        expected_version=payload.expected_version,
        operation_id=str(payload.operation_id),
        employee_no=payload.employee_no,
        change_user_link="user_id" in payload.model_fields_set,
        user_id=_optional_identifier(payload.user_id),
        display_name=payload.display_name,
        department=payload.department,
        position=payload.position,
        office_zone=payload.office_zone,
        is_active=payload.is_active,
    )


@api_router.get("", response_model=EmployeeListResponse)
def list_employees(
    search: str | None = Query(default=None, max_length=100),
    department: str | None = Query(default=None, max_length=100),
    status_filter: EmployeeStatus | None = Query(default=None, alias="status"),
    is_active: bool | None = None,
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    actor: User = Depends(get_current_user),
    service: EmployeeService = Depends(get_employee_service),
    settings: Settings = Depends(get_settings),
) -> EmployeeListResponse:
    resolved_limit, resolved_offset = _resolve_paging(limit, offset, settings)
    page = service.list_employees(
        actor,
        limit=resolved_limit,
        offset=resolved_offset,
        search=search,
        department=department,
        status=status_filter,
        is_active=is_active,
    )
    return EmployeeListResponse.from_page(
        page, limit=resolved_limit, offset=resolved_offset
    )


@api_router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=EmployeeResponse,
)
def create_employee(
    payload: CreateEmployeeRequest,
    response: Response,
    _: None = Depends(require_csrf),
    actor: User = Depends(require_admin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: EmployeeService = Depends(get_employee_service),
) -> EmployeeResponse:
    employee = service.create_employee(
        actor, _create_command(payload), ip_fingerprint=ip_fingerprint
    )
    response.headers["Location"] = f"/api/v1/employees/{employee.id}"
    return EmployeeResponse.from_employee(employee)


@api_router.get("/{employee_id}", response_model=EmployeeResponse)
def get_employee(
    employee_id: str,
    actor: User = Depends(get_current_user),
    service: EmployeeService = Depends(get_employee_service),
) -> EmployeeResponse:
    return EmployeeResponse.from_employee(service.get_employee(actor, employee_id))


@api_router.patch("/{employee_id}", response_model=EmployeeResponse)
def update_employee(
    employee_id: str,
    payload: UpdateEmployeeRequest,
    _: None = Depends(require_csrf),
    actor: User = Depends(require_admin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: EmployeeService = Depends(get_employee_service),
) -> EmployeeResponse:
    employee = service.update_employee(
        actor,
        _update_command(employee_id, payload),
        ip_fingerprint=ip_fingerprint,
    )
    return EmployeeResponse.from_employee(employee)


@api_router.delete(
    "/{employee_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def deactivate_employee(
    employee_id: str,
    payload: DeactivateEmployeeRequest,
    _: None = Depends(require_csrf),
    actor: User = Depends(require_admin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    coordinator: EmployeeInterviewCoordinator = Depends(
        get_employee_interview_coordinator
    ),
) -> Response:
    coordinator.deactivate_employee(
        actor,
        employee_id,
        expected_version=payload.expected_version,
        operation_id=str(payload.operation_id),
        ip_fingerprint=ip_fingerprint,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@api_router.get(
    "/{employee_id}/status-history",
    response_model=EmployeeStatusHistoryListResponse,
)
def list_status_history(
    employee_id: str,
    source: StatusSource | None = None,
    from_status: EmployeeStatus | None = Query(default=None, alias="from"),
    to_status: EmployeeStatus | None = Query(default=None, alias="to"),
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    actor: User = Depends(get_current_user),
    service: EmployeeService = Depends(get_employee_service),
    settings: Settings = Depends(get_settings),
) -> EmployeeStatusHistoryListResponse:
    resolved_limit, resolved_offset = _resolve_paging(limit, offset, settings)
    page = service.list_status_history(
        actor,
        employee_id,
        limit=resolved_limit,
        offset=resolved_offset,
        source=source,
        from_status=from_status,
        to_status=to_status,
    )
    return EmployeeStatusHistoryListResponse.from_page(
        page, limit=resolved_limit, offset=resolved_offset
    )


@api_router.put(
    "/{employee_id}/status-override",
    response_model=EmployeeResponse,
)
def set_status_override(
    employee_id: str,
    payload: SetStatusOverrideRequest,
    _: None = Depends(require_csrf),
    actor: User = Depends(get_current_user),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: EmployeeService = Depends(get_employee_service),
) -> EmployeeResponse:
    employee = service.set_status_override(
        actor,
        SetStatusOverrideCommand(
            employee_id=employee_id,
            status=payload.status,
            reason=payload.reason,
            ends_at=payload.ends_at,
            expected_version=payload.expected_version,
            operation_id=str(payload.operation_id),
        ),
        ip_fingerprint=ip_fingerprint,
    )
    return EmployeeResponse.from_employee(employee)


@api_router.delete(
    "/{employee_id}/status-override",
    response_model=EmployeeResponse,
)
def clear_status_override(
    employee_id: str,
    payload: ClearStatusOverrideRequest,
    _: None = Depends(require_csrf),
    actor: User = Depends(get_current_user),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    coordinator: EmployeeInterviewCoordinator = Depends(
        get_employee_interview_coordinator
    ),
) -> EmployeeResponse:
    employee = coordinator.clear_status_override(
        actor,
        ClearStatusOverrideCommand(
            employee_id=employee_id,
            expected_version=payload.expected_version,
            operation_id=str(payload.operation_id),
        ),
        ip_fingerprint=ip_fingerprint,
    )
    return EmployeeResponse.from_employee(employee)


@evaluation_api_router.post("", response_model=EmployeeStatusEvaluationResponse)
def evaluate_employee_statuses(
    payload: EvaluateEmployeeStatusesRequest,
    _: None = Depends(require_csrf),
    actor: User = Depends(require_admin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: EmployeeService = Depends(get_employee_service),
) -> EmployeeStatusEvaluationResponse:
    result = service.evaluate_statuses(
        actor,
        EvaluateEmployeeStatusesCommand(operation_id=str(payload.operation_id)),
        ip_fingerprint=ip_fingerprint,
    )
    return EmployeeStatusEvaluationResponse.from_evaluation(result)


@development_api_router.post("", response_model=EmployeeObservationResponse)
def record_mock_observation(
    payload: MockEmployeeObservationRequest,
    _: None = Depends(require_csrf),
    actor: User = Depends(require_admin),
    coordinator: EmployeeInterviewCoordinator = Depends(
        get_employee_interview_coordinator
    ),
) -> EmployeeObservationResponse:
    observation = coordinator.record_mock_observation(
        actor,
        RecordEmployeeObservationCommand(
            event_id=str(payload.event_id),
            employee_id=payload.employee_id,
            person_present=payload.person_present,
            phone_detected=payload.phone_detected,
            confidence=payload.confidence,
            observed_at=payload.observed_at,
        ),
    )
    return EmployeeObservationResponse.from_observation(observation)


@page_router.get("")
def employees_page(
    request: Request,
    search: str | None = Query(default=None, max_length=100),
    department: str | None = Query(default=None, max_length=100),
    status_filter: EmployeeStatus | None = Query(default=None, alias="status"),
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    actor: User = Depends(get_current_page_user),
    service: EmployeeService = Depends(get_employee_service),
    settings: Settings = Depends(get_settings),
):
    resolved_limit, resolved_offset = _resolve_paging(limit, offset, settings)
    page = service.list_employees(
        actor,
        limit=resolved_limit,
        offset=resolved_offset,
        search=search,
        department=department,
        status=status_filter,
        is_active=True,
    )
    return templates.TemplateResponse(
        request=request,
        name="employees/list.html",
        context=_page_context(
            request,
            actor,
            page=page,
            statuses=list(EmployeeStatus),
            selected_status=status_filter,
            search=search or "",
            department=department or "",
            limit=resolved_limit,
            offset=resolved_offset,
            has_prev=resolved_offset > 0,
            has_next=resolved_offset + resolved_limit < page.total,
            show_employee_dev_tools=settings.mock_inputs_enabled,
            present_employee_ids={
                employee.id for employee in page.items if service.is_present(employee)
            },
        ),
    )


@page_router.get("/{employee_id}")
def employee_detail_page(
    request: Request,
    employee_id: str,
    actor: User = Depends(get_current_page_user),
    service: EmployeeService = Depends(get_employee_service),
):
    return _render_employee_detail(request, actor=actor, service=service, employee_id=employee_id)


@page_router.post("/{employee_id}/status-override")
def set_status_override_page(
    request: Request,
    employee_id: str,
    form: Annotated[SetStatusOverrideForm, Form()],
    _: None = Depends(require_csrf),
    actor: User = Depends(get_current_page_user),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: EmployeeService = Depends(get_employee_service),
):
    try:
        service.set_status_override(
            actor,
            SetStatusOverrideCommand(
                employee_id=employee_id,
                status=form.status,
                reason=form.reason,
                ends_at=form.ends_at,
                expected_version=form.expected_version,
                operation_id=str(form.operation_id),
            ),
            ip_fingerprint=ip_fingerprint,
        )
    except DomainError as exc:
        return _render_employee_detail(
            request,
            actor=actor,
            service=service,
            employee_id=employee_id,
            error=exc.message,
            status_code=exc.status_code,
        )
    return RedirectResponse(
        url=f"/employees/{employee_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@page_router.post("/{employee_id}/status-override/clear")
def clear_status_override_page(
    request: Request,
    employee_id: str,
    form: Annotated[ClearStatusOverrideForm, Form()],
    _: None = Depends(require_csrf),
    actor: User = Depends(get_current_page_user),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: EmployeeService = Depends(get_employee_service),
    coordinator: EmployeeInterviewCoordinator = Depends(
        get_employee_interview_coordinator
    ),
):
    try:
        coordinator.clear_status_override(
            actor,
            ClearStatusOverrideCommand(
                employee_id=employee_id,
                expected_version=form.expected_version,
                operation_id=str(form.operation_id),
            ),
            ip_fingerprint=ip_fingerprint,
        )
    except DomainError as exc:
        return _render_employee_detail(
            request,
            actor=actor,
            service=service,
            employee_id=employee_id,
            error=exc.message,
            status_code=exc.status_code,
        )
    return RedirectResponse(
        url=f"/employees/{employee_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@admin_page_router.get("")
def admin_employees_page(
    request: Request,
    evaluated: int | None = Query(default=None, ge=0),
    changed: int | None = Query(default=None, ge=0),
    actor: User = Depends(require_page_admin),
    service: EmployeeService = Depends(get_employee_service),
    user_service: UserService = Depends(get_user_service),
    settings: Settings = Depends(get_settings),
):
    return _render_admin_employees(
        request,
        actor=actor,
        service=service,
        user_service=user_service,
        settings=settings,
        evaluated=evaluated,
        changed=changed,
    )


@admin_page_router.post("")
def create_employee_page(
    request: Request,
    form: Annotated[CreateEmployeeForm, Form()],
    _: None = Depends(require_csrf),
    actor: User = Depends(require_page_admin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: EmployeeService = Depends(get_employee_service),
    user_service: UserService = Depends(get_user_service),
    settings: Settings = Depends(get_settings),
):
    try:
        service.create_employee(
            actor, _create_command(form), ip_fingerprint=ip_fingerprint
        )
    except DomainError as exc:
        return _render_admin_employees(
            request,
            actor=actor,
            service=service,
            user_service=user_service,
            settings=settings,
            error=exc.message,
            status_code=exc.status_code,
        )
    return RedirectResponse(url="/admin/employees", status_code=status.HTTP_303_SEE_OTHER)


@admin_page_router.post("/{employee_id}/update")
def update_employee_page(
    request: Request,
    employee_id: str,
    form: Annotated[UpdateEmployeeForm, Form()],
    _: None = Depends(require_csrf),
    actor: User = Depends(require_page_admin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: EmployeeService = Depends(get_employee_service),
    user_service: UserService = Depends(get_user_service),
    settings: Settings = Depends(get_settings),
):
    try:
        service.update_employee(
            actor,
            _update_command(employee_id, form),
            ip_fingerprint=ip_fingerprint,
        )
    except DomainError as exc:
        return _render_admin_employees(
            request,
            actor=actor,
            service=service,
            user_service=user_service,
            settings=settings,
            error=exc.message,
            status_code=exc.status_code,
        )
    return RedirectResponse(url="/admin/employees", status_code=status.HTTP_303_SEE_OTHER)


@admin_page_router.post("/{employee_id}/deactivate")
def deactivate_employee_page(
    request: Request,
    employee_id: str,
    form: Annotated[DeactivateEmployeeForm, Form()],
    _: None = Depends(require_csrf),
    actor: User = Depends(require_page_admin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: EmployeeService = Depends(get_employee_service),
    coordinator: EmployeeInterviewCoordinator = Depends(
        get_employee_interview_coordinator
    ),
    user_service: UserService = Depends(get_user_service),
    settings: Settings = Depends(get_settings),
):
    try:
        coordinator.deactivate_employee(
            actor,
            employee_id,
            expected_version=form.expected_version,
            operation_id=str(form.operation_id),
            ip_fingerprint=ip_fingerprint,
        )
    except DomainError as exc:
        return _render_admin_employees(
            request,
            actor=actor,
            service=service,
            user_service=user_service,
            settings=settings,
            error=exc.message,
            status_code=exc.status_code,
        )
    return RedirectResponse(url="/admin/employees", status_code=status.HTTP_303_SEE_OTHER)


@admin_page_router.post("/evaluate")
def evaluate_employee_statuses_page(
    form: Annotated[EvaluateEmployeeStatusesForm, Form()],
    _: None = Depends(require_csrf),
    actor: User = Depends(require_page_admin),
    ip_fingerprint: str = Depends(request_ip_fingerprint),
    service: EmployeeService = Depends(get_employee_service),
):
    result = service.evaluate_statuses(
        actor,
        EvaluateEmployeeStatusesCommand(operation_id=str(form.operation_id)),
        ip_fingerprint=ip_fingerprint,
    )
    return RedirectResponse(
        url=(
            f"/admin/employees?evaluated={result.evaluated_count}"
            f"&changed={result.changed_count}"
        ),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@development_page_router.get("")
def development_tools_page(
    request: Request,
    actor: User = Depends(require_page_admin),
    service: EmployeeService = Depends(get_employee_service),
    settings: Settings = Depends(get_settings),
):
    return _render_development_tools(
        request, actor=actor, service=service, settings=settings
    )


@development_page_router.post("")
def record_mock_observation_page(
    request: Request,
    form: Annotated[MockEmployeeObservationForm, Form()],
    _: None = Depends(require_csrf),
    actor: User = Depends(require_page_admin),
    service: EmployeeService = Depends(get_employee_service),
    coordinator: EmployeeInterviewCoordinator = Depends(
        get_employee_interview_coordinator
    ),
    settings: Settings = Depends(get_settings),
):
    try:
        observation = coordinator.record_mock_observation(
            actor,
            RecordEmployeeObservationCommand(
                event_id=str(form.event_id),
                employee_id=form.employee_id,
                person_present=form.person_present,
                phone_detected=form.phone_detected,
                confidence=form.confidence,
                observed_at=form.observed_at,
            ),
        )
    except DomainError as exc:
        return _render_development_tools(
            request,
            actor=actor,
            service=service,
            settings=settings,
            error=exc.message,
            status_code=exc.status_code,
        )
    return _render_development_tools(
        request,
        actor=actor,
        service=service,
        settings=settings,
        success=(
            f"관측을 반영했습니다: {observation.resulting_status.value}"
            f" (상태 변경: {'예' if observation.status_changed else '아니요'})"
        ),
    )


def _render_employee_detail(
    request: Request,
    *,
    actor: User,
    service: EmployeeService,
    employee_id: str,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
):
    employee = service.get_employee(actor, employee_id)
    history = service.list_status_history(
        actor,
        employee_id,
        limit=20,
        offset=0,
    )
    return templates.TemplateResponse(
        request=request,
        name="employees/detail.html",
        context=_page_context(
            request,
            actor,
            employee=employee,
            history=history,
            can_override=service.can_override(actor, employee),
            override_statuses=[EmployeeStatus.AWAY, EmployeeStatus.OFFSITE],
            set_operation_id=str(uuid4()),
            clear_operation_id=str(uuid4()),
            wait_operation_id=str(uuid4()),
            employee_is_present=service.is_present(employee),
            error=error,
        ),
        status_code=status_code,
    )


def _render_admin_employees(
    request: Request,
    *,
    actor: User,
    service: EmployeeService,
    user_service: UserService,
    settings: Settings,
    evaluated: int | None = None,
    changed: int | None = None,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
):
    page = service.list_employees(
        actor,
        limit=settings.page_size_max,
        offset=0,
    )
    staff_page = user_service.list_users(
        actor,
        limit=settings.page_size_max,
        offset=0,
        role=UserRole.STAFF,
        status=UserStatus.ACTIVE,
    )
    return templates.TemplateResponse(
        request=request,
        name="admin/employees/list.html",
        context=_page_context(
            request,
            actor,
            page=page,
            staff_users=staff_page.items,
            create_operation_id=str(uuid4()),
            evaluate_operation_id=str(uuid4()),
            row_operation_ids={employee.id: str(uuid4()) for employee in page.items},
            deactivate_operation_ids={
                employee.id: str(uuid4()) for employee in page.items
            },
            evaluated=evaluated,
            changed=changed,
            error=error,
            show_employee_dev_tools=settings.mock_inputs_enabled,
        ),
        status_code=status_code,
    )


def _render_development_tools(
    request: Request,
    *,
    actor: User,
    service: EmployeeService,
    settings: Settings,
    error: str | None = None,
    success: str | None = None,
    status_code: int = status.HTTP_200_OK,
):
    page = service.list_employees(
        actor,
        limit=settings.page_size_max,
        offset=0,
        is_active=True,
    )
    return templates.TemplateResponse(
        request=request,
        name="admin/dev_tools/index.html",
        context=_page_context(
            request,
            actor,
            employees=page.items,
            event_id=str(uuid4()),
            observed_at=datetime.now(UTC).isoformat(),
            error=error,
            success=success,
            show_employee_dev_tools=True,
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
        **values,
    }
