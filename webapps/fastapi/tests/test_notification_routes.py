"""알림 API, 화면, 권한과 사용자 여정 테스트."""

from __future__ import annotations

from collections.abc import Iterator
from typing import NoReturn
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from app.auth.dependencies import (
    CSRF_COOKIE,
    get_current_page_user,
    get_current_user,
    require_csrf,
)
from app.main import app, handle_domain_error, include_notification_routers
from app.notifications.adapters.memory_repository import InMemoryNotificationRepository
from app.notifications.models import CreateNotificationCommand, Notification
from app.notifications.router import development_page_router
from app.notifications.service import NotificationService
from app.shared.dependencies import (
    get_auth_service,
    get_notification_service,
    get_settings,
    get_user_service,
)
from app.shared.errors import DomainError, RepositoryUnavailableError
from app.shared.templating import STATIC_DIR
from app.users.models import User, UserRole
from tests.auth_helpers import AuthStack, build_auth_stack
from tests.settings_helpers import make_settings

ORIGIN = "http://testserver"
PASSWORD = "ValidPassword1!"
type NotificationStack = tuple[AuthStack, InMemoryNotificationRepository, NotificationService]


@pytest.fixture
def notification_stack() -> NotificationStack:
    auth = build_auth_stack()
    repository = InMemoryNotificationRepository()
    service = NotificationService(
        repository,
        auth.users,
        clock=auth.clock,
        mock_delivery_mode="success",
    )
    return auth, repository, service


@pytest.fixture
def notification_client(notification_stack: NotificationStack) -> Iterator[TestClient]:
    auth, _, service = notification_stack
    app.dependency_overrides[get_auth_service] = lambda: auth.auth_service
    app.dependency_overrides[get_user_service] = lambda: auth.user_service
    app.dependency_overrides[get_notification_service] = lambda: service
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _login(client: TestClient, user: User) -> None:
    response = client.post(
        "/api/v1/auth/login",
        headers={"Origin": ORIGIN},
        json={"email": user.email, "password": PASSWORD},
    )
    assert response.status_code == 200


def _csrf_headers(client: TestClient) -> dict[str, str]:
    return {"Origin": ORIGIN, "X-CSRF-Token": client.cookies[CSRF_COOKIE]}


def _create(service: NotificationService, user_id: str, suffix: str = "1") -> Notification:
    return service.create(
        CreateNotificationCommand(
            recipient_user_id=user_id,
            type="INTERVIEW_READY",
            title=f"면담 준비 완료 {suffix}",
            body="담당 직원이 복귀했습니다.",
            data={"target_route": "/my/interview-waits", "wait_id": f"wait-{suffix}"},
            operation_id=f"create-{suffix}",
            dedupe_key=f"wait-ready:{suffix}",
        )
    )


def test_알림_API는_본인목록_filter_paging_개별과_전체읽음을_제공한다(
    notification_client: TestClient,
    notification_stack: NotificationStack,
) -> None:
    auth, _, service = notification_stack
    user = auth.seed(UserRole.STUDENT)
    first = _create(service, user.id, "1")
    _create(service, user.id, "2")
    _login(notification_client, user)

    page = notification_client.get(
        "/api/v1/notifications?type=INTERVIEW_READY&is_read=false&limit=1&offset=0"
    )
    unread = notification_client.get("/api/v1/notifications/unread-count")

    assert page.status_code == 200
    assert page.json()["total"] == 2
    assert page.json()["limit"] == 1
    assert page.json()["items"][0]["target_route"] == "/my/interview-waits"
    assert unread.json() == {"unread_count": 2}

    marked = notification_client.patch(
        f"/api/v1/notifications/{first.id}",
        headers=_csrf_headers(notification_client),
        json={"operation_id": str(uuid4())},
    )
    batch = notification_client.post(
        "/api/v1/notification-read-batches",
        headers=_csrf_headers(notification_client),
        json={"operation_id": str(uuid4())},
    )

    assert marked.status_code == 200
    assert marked.json()["is_read"] is True
    assert batch.status_code == 200
    assert batch.json() == {"updated_count": 1}
    assert notification_client.get("/api/v1/notifications/unread-count").json() == {
        "unread_count": 0
    }


def test_다른사용자의_알림읽음은_존재를_숨긴_404다(
    notification_client: TestClient,
    notification_stack: NotificationStack,
) -> None:
    auth, _, service = notification_stack
    owner = auth.seed(UserRole.STUDENT)
    other = auth.seed(UserRole.STAFF)
    notification = _create(service, owner.id)
    _login(notification_client, other)

    response = notification_client.patch(
        f"/api/v1/notifications/{notification.id}",
        headers=_csrf_headers(notification_client),
        json={"operation_id": str(uuid4())},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOTIFICATION_NOT_FOUND"


def test_알림화면은_빈상태_badge_읽음여정을_표현한다(
    notification_client: TestClient,
    notification_stack: NotificationStack,
) -> None:
    auth, _, service = notification_stack
    user = auth.seed(UserRole.STUDENT)
    _login(notification_client, user)

    empty = notification_client.get("/notifications")
    assert empty.status_code == 200
    assert "표시할 알림이 없습니다" in empty.text

    notification = _create(service, user.id)
    event_page = notification_client.get("/events")
    inbox = notification_client.get("/notifications")
    assert 'aria-label="읽지 않은 알림 1개"' in event_page.text
    assert notification.title in inbox.text
    assert "연결 화면 열기" in inbox.text

    marked = notification_client.patch(
        f"/api/v1/notifications/{notification.id}",
        headers=_csrf_headers(notification_client),
        json={"operation_id": str(uuid4())},
    )
    assert marked.status_code == 200
    assert 'aria-label="읽지 않은 알림' not in notification_client.get("/events").text


def test_알림저장소_오류는_내부정보없는_오류화면이_된다(
    notification_client: TestClient,
    notification_stack: NotificationStack,
) -> None:
    auth, _, _ = notification_stack
    user = auth.seed(UserRole.STUDENT)
    _login(notification_client, user)

    class FailingRepository:
        def count_unread(self, recipient_user_id: str) -> int:
            raise RepositoryUnavailableError()

    failing_service = NotificationService(
        FailingRepository(),  # type: ignore[arg-type]
        auth.users,
        clock=auth.clock,
        mock_delivery_mode=None,
    )
    app.dependency_overrides[get_notification_service] = lambda: failing_service

    response = notification_client.get("/notifications")

    assert response.status_code == 503
    assert "데이터 저장소를 일시적으로 사용할 수 없습니다" in response.text
    assert "Traceback" not in response.text


def test_mock_delivery_API는_개발환경_ADMIN에만_등록되고_명시적_재시도한다(
    notification_stack: NotificationStack,
) -> None:
    auth, _, _ = notification_stack
    admin = auth.seed(UserRole.ADMIN)
    repository = InMemoryNotificationRepository()
    service = NotificationService(
        repository,
        auth.users,
        clock=auth.clock,
        mock_delivery_mode="fail_once",
    )
    notification = _create(service, admin.id)
    settings = make_settings(
        _env_file=None,
        app_env="local",
        database_mode="memory",
        mock_inputs_enabled=True,
        notification_mock_delivery_mode="success",
        web_origin=ORIGIN,
    )
    application = FastAPI()
    include_notification_routers(application, settings)
    application.dependency_overrides[get_current_user] = lambda: admin
    application.dependency_overrides[require_csrf] = lambda: None
    application.dependency_overrides[get_notification_service] = lambda: service
    application.dependency_overrides[get_settings] = lambda: settings

    with TestClient(application) as client:
        listed = client.get("/api/v1/admin/mock-deliveries")
        retry = client.post(
            "/api/v1/admin/mock-delivery-attempts",
            json={
                "notification_id": notification.id,
                "operation_id": str(uuid4()),
            },
        )

    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert retry.status_code == 201
    assert retry.headers["location"] == "/api/v1/admin/mock-deliveries"
    assert retry.json()["attempt"] == 2
    assert retry.json()["status"] == "SUCCESS"


def test_production에는_mock_delivery_router가_등록되지_않는다() -> None:
    settings = make_settings(
        _env_file=None,
        app_env="prod",
        database_mode="mongodb",
        database_url="mongodb://example.invalid",
        database_name="smart_office",
        mock_inputs_enabled=False,
        web_origin=ORIGIN,
    )
    application = FastAPI()
    include_notification_routers(application, settings)
    paths = set(application.openapi()["paths"])

    assert "/api/v1/notifications" in paths
    assert "/api/v1/admin/mock-deliveries" not in paths
    assert "/api/v1/admin/mock-delivery-attempts" not in paths
    assert "/admin/mock-deliveries" not in paths


def test_mock_delivery_화면은_권한없음을_명시한다(
    notification_stack: NotificationStack,
) -> None:
    auth, _, service = notification_stack
    student = auth.seed(UserRole.STUDENT)
    settings = make_settings(
        _env_file=None,
        app_env="local",
        database_mode="memory",
        mock_inputs_enabled=True,
        web_origin=ORIGIN,
    )
    application = FastAPI()
    application.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    application.include_router(development_page_router)
    application.add_exception_handler(
        DomainError,
        handle_domain_error,  # type: ignore[arg-type]
    )
    application.dependency_overrides[get_current_page_user] = lambda: student
    application.dependency_overrides[get_notification_service] = lambda: service
    application.dependency_overrides[get_settings] = lambda: settings

    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get("/admin/mock-deliveries")

    assert response.status_code == 403
    assert "권한이 없습니다" in response.text


def test_mock_delivery_화면은_빈상태_정상상태_저장소오류를_구분한다(
    notification_stack: NotificationStack,
) -> None:
    auth, _, _ = notification_stack
    admin = auth.seed(UserRole.ADMIN)
    repository = InMemoryNotificationRepository()
    service = NotificationService(
        repository,
        auth.users,
        clock=auth.clock,
        mock_delivery_mode="fail_once",
    )
    settings = make_settings(
        _env_file=None,
        app_env="local",
        database_mode="memory",
        mock_inputs_enabled=True,
        web_origin=ORIGIN,
    )
    application = FastAPI()
    application.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    application.include_router(development_page_router)
    application.add_exception_handler(
        DomainError,
        handle_domain_error,  # type: ignore[arg-type]
    )
    application.dependency_overrides[get_current_page_user] = lambda: admin
    application.dependency_overrides[get_notification_service] = lambda: service
    application.dependency_overrides[get_settings] = lambda: settings

    with TestClient(application, raise_server_exceptions=False) as client:
        empty = client.get("/admin/mock-deliveries")
        notification = _create(service, admin.id)
        normal = client.get("/admin/mock-deliveries")

        class FailingRepository:
            def list_deliveries(self, **kwargs: object) -> NoReturn:
                raise RepositoryUnavailableError()

        failing_service = NotificationService(
            FailingRepository(),  # type: ignore[arg-type]
            auth.users,
            clock=auth.clock,
            mock_delivery_mode="success",
        )
        application.dependency_overrides[get_notification_service] = lambda: failing_service
        failed = client.get("/admin/mock-deliveries")

    assert empty.status_code == 200
    assert "기록된 mock delivery가 없습니다" in empty.text
    assert normal.status_code == 200
    assert notification.id in normal.text
    assert "TEMPORARY_FAILURE" in normal.text
    assert "담당 직원이 복귀했습니다" not in normal.text
    assert failed.status_code == 503
    assert "데이터 저장소를 일시적으로 사용할 수 없습니다" in failed.text
