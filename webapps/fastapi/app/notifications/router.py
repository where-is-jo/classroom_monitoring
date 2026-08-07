"""알림 API와 Jinja2 화면의 HTTP 경계."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, Form, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from ..auth.dependencies import (
    CSRF_COOKIE,
    get_current_page_user,
    get_current_user,
    require_admin,
    require_csrf,
    require_page_admin,
)
from ..shared.config import Settings
from ..shared.dependencies import get_notification_service, get_settings
from ..shared.errors import DomainError
from ..shared.templating import templates
from ..users.models import ADMIN_ROLES, User
from .models import (
    MockDelivery,
    MockDeliveryStatus,
    Notification,
    RetryMockDeliveryCommand,
)
from .schemas import (
    MockDeliveryAttemptRequest,
    MockDeliveryListResponse,
    MockDeliveryResponse,
    NotificationListResponse,
    NotificationReadBatchRequest,
    NotificationReadBatchResponse,
    NotificationReadRequest,
    NotificationResponse,
    NotificationUnreadCountResponse,
)
from .service import NotificationService

api_router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])
read_batch_api_router = APIRouter(
    prefix="/api/v1/notification-read-batches", tags=["notifications"]
)
development_api_router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
page_router = APIRouter(prefix="/notifications", tags=["notification-pages"])
development_page_router = APIRouter(prefix="/admin/mock-deliveries", tags=["admin-pages"])


def _resolve_paging(limit: int | None, offset: int, settings: Settings) -> tuple[int, int]:
    return min(limit or settings.page_size_default, settings.page_size_max), offset


def _notification_response(
    notification: Notification, service: NotificationService
) -> NotificationResponse:
    return NotificationResponse(
        id=notification.id,
        type=notification.type,
        title=notification.title,
        body=notification.body,
        data=notification.data,
        target_route=service.target_route(notification),
        is_read=notification.is_read,
        read_at=notification.read_at,
        created_at=notification.created_at,
    )


def _delivery_response(delivery: MockDelivery) -> MockDeliveryResponse:
    return MockDeliveryResponse(
        id=delivery.id,
        notification_id=delivery.notification_id,
        provider=delivery.provider,
        status=delivery.status,
        attempt=delivery.attempt,
        request_payload=delivery.request_payload,
        result_payload=delivery.result_payload,
        error=delivery.error,
        attempted_at=delivery.attempted_at,
    )


@api_router.get("", response_model=NotificationListResponse)
def list_notifications(
    is_read: bool | None = None,
    notification_type: str | None = Query(default=None, alias="type"),
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    actor: User = Depends(get_current_user),
    service: NotificationService = Depends(get_notification_service),
    settings: Settings = Depends(get_settings),
) -> NotificationListResponse:
    resolved_limit, resolved_offset = _resolve_paging(limit, offset, settings)
    page = service.list_notifications(
        actor,
        is_read=is_read,
        notification_type=notification_type,
        limit=resolved_limit,
        offset=resolved_offset,
    )
    return NotificationListResponse(
        items=[_notification_response(item, service) for item in page.items],
        total=page.total,
        limit=resolved_limit,
        offset=resolved_offset,
    )


@api_router.get("/unread-count", response_model=NotificationUnreadCountResponse)
def unread_count(
    actor: User = Depends(get_current_user),
    service: NotificationService = Depends(get_notification_service),
) -> NotificationUnreadCountResponse:
    return NotificationUnreadCountResponse(unread_count=service.unread_count(actor))


@api_router.patch("/{notification_id}", response_model=NotificationResponse)
def mark_notification_read(
    notification_id: str,
    payload: NotificationReadRequest,
    _: None = Depends(require_csrf),
    actor: User = Depends(get_current_user),
    service: NotificationService = Depends(get_notification_service),
) -> NotificationResponse:
    notification = service.mark_read(actor, notification_id, operation_id=str(payload.operation_id))
    return _notification_response(notification, service)


@read_batch_api_router.post("", response_model=NotificationReadBatchResponse)
def mark_all_notifications_read(
    payload: NotificationReadBatchRequest,
    _: None = Depends(require_csrf),
    actor: User = Depends(get_current_user),
    service: NotificationService = Depends(get_notification_service),
) -> NotificationReadBatchResponse:
    return NotificationReadBatchResponse(
        updated_count=service.mark_all_read(actor, operation_id=str(payload.operation_id))
    )


@development_api_router.get("/mock-deliveries", response_model=MockDeliveryListResponse)
def list_mock_deliveries(
    delivery_status: MockDeliveryStatus | None = Query(default=None, alias="status"),
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(require_admin),
    service: NotificationService = Depends(get_notification_service),
    settings: Settings = Depends(get_settings),
) -> MockDeliveryListResponse:
    resolved_limit, resolved_offset = _resolve_paging(limit, offset, settings)
    page = service.list_mock_deliveries(
        status=delivery_status,
        limit=resolved_limit,
        offset=resolved_offset,
    )
    return MockDeliveryListResponse(
        items=[_delivery_response(item) for item in page.items],
        total=page.total,
        limit=resolved_limit,
        offset=resolved_offset,
    )


@development_api_router.post(
    "/mock-delivery-attempts",
    response_model=MockDeliveryResponse,
    status_code=status.HTTP_201_CREATED,
)
def retry_mock_delivery(
    payload: MockDeliveryAttemptRequest,
    response: Response,
    _: None = Depends(require_csrf),
    __: User = Depends(require_admin),
    service: NotificationService = Depends(get_notification_service),
) -> MockDeliveryResponse:
    delivery = service.retry_mock_delivery(
        RetryMockDeliveryCommand(
            notification_id=payload.notification_id,
            operation_id=str(payload.operation_id),
        )
    )
    response.headers["Location"] = "/api/v1/admin/mock-deliveries"
    return _delivery_response(delivery)


@page_router.get("")
def notifications_page(
    request: Request,
    is_read: bool | None = None,
    notification_type: str | None = Query(default=None, alias="type"),
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    actor: User = Depends(get_current_page_user),
    service: NotificationService = Depends(get_notification_service),
    settings: Settings = Depends(get_settings),
) -> Response:
    return _render_notifications(
        request,
        actor=actor,
        service=service,
        settings=settings,
        is_read=is_read,
        notification_type=notification_type,
        limit=limit,
        offset=offset,
    )


@page_router.post("/{notification_id}/read")
def mark_notification_read_page(
    request: Request,
    notification_id: str,
    operation_id: str = Form(...),
    _: None = Depends(require_csrf),
    actor: User = Depends(get_current_page_user),
    service: NotificationService = Depends(get_notification_service),
    settings: Settings = Depends(get_settings),
) -> Response:
    try:
        service.mark_read(actor, notification_id, operation_id=operation_id)
    except DomainError as exc:
        return _render_notifications(
            request,
            actor=actor,
            service=service,
            settings=settings,
            error=exc.message,
            status_code=exc.status_code,
        )
    return RedirectResponse(url="/notifications", status_code=status.HTTP_303_SEE_OTHER)


@page_router.post("/read-all")
def mark_all_notifications_read_page(
    operation_id: str = Form(...),
    _: None = Depends(require_csrf),
    actor: User = Depends(get_current_page_user),
    service: NotificationService = Depends(get_notification_service),
) -> RedirectResponse:
    service.mark_all_read(actor, operation_id=operation_id)
    return RedirectResponse(url="/notifications", status_code=status.HTTP_303_SEE_OTHER)


@development_page_router.get("")
def mock_deliveries_page(
    request: Request,
    delivery_status: MockDeliveryStatus | None = Query(default=None, alias="status"),
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    actor: User = Depends(require_page_admin),
    service: NotificationService = Depends(get_notification_service),
    settings: Settings = Depends(get_settings),
) -> Response:
    return _render_mock_deliveries(
        request,
        actor=actor,
        service=service,
        settings=settings,
        delivery_status=delivery_status,
        limit=limit,
        offset=offset,
    )


@development_page_router.post("/retry")
def retry_mock_delivery_page(
    request: Request,
    notification_id: str = Form(...),
    operation_id: str = Form(...),
    _: None = Depends(require_csrf),
    actor: User = Depends(require_page_admin),
    service: NotificationService = Depends(get_notification_service),
    settings: Settings = Depends(get_settings),
) -> Response:
    try:
        service.retry_mock_delivery(
            RetryMockDeliveryCommand(
                notification_id=notification_id,
                operation_id=operation_id,
            )
        )
    except DomainError as exc:
        return _render_mock_deliveries(
            request,
            actor=actor,
            service=service,
            settings=settings,
            error=exc.message,
            status_code=exc.status_code,
        )
    return RedirectResponse(url="/admin/mock-deliveries", status_code=status.HTTP_303_SEE_OTHER)


def _render_notifications(
    request: Request,
    *,
    actor: User,
    service: NotificationService,
    settings: Settings,
    is_read: bool | None = None,
    notification_type: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    resolved_limit, resolved_offset = _resolve_paging(limit, offset, settings)
    page = service.list_notifications(
        actor,
        is_read=is_read,
        notification_type=notification_type,
        limit=resolved_limit,
        offset=resolved_offset,
    )
    return templates.TemplateResponse(
        request=request,
        name="notifications/list.html",
        context=_page_context(
            request,
            actor,
            page=page,
            target_routes={item.id: service.target_route(item) for item in page.items},
            is_read=is_read,
            notification_type=notification_type or "",
            limit=resolved_limit,
            offset=resolved_offset,
            has_prev=resolved_offset > 0,
            has_next=resolved_offset + resolved_limit < page.total,
            row_operation_ids={item.id: str(uuid4()) for item in page.items},
            batch_operation_id=str(uuid4()),
            error=error,
        ),
        status_code=status_code,
    )


def _render_mock_deliveries(
    request: Request,
    *,
    actor: User,
    service: NotificationService,
    settings: Settings,
    delivery_status: MockDeliveryStatus | None = None,
    limit: int | None = None,
    offset: int = 0,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    resolved_limit, resolved_offset = _resolve_paging(limit, offset, settings)
    page = service.list_mock_deliveries(
        status=delivery_status,
        limit=resolved_limit,
        offset=resolved_offset,
    )
    return templates.TemplateResponse(
        request=request,
        name="admin/mock_deliveries/list.html",
        context=_page_context(
            request,
            actor,
            page=page,
            statuses=list(MockDeliveryStatus),
            selected_status=delivery_status,
            limit=resolved_limit,
            offset=resolved_offset,
            has_prev=resolved_offset > 0,
            has_next=resolved_offset + resolved_limit < page.total,
            retry_operation_ids={item.id: str(uuid4()) for item in page.items},
            error=error,
            show_notification_dev_tools=True,
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
