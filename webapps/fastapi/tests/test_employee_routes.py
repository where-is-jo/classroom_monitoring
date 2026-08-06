"""직원 API, Jinja2 화면과 조건부 mock router 테스트."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from app.auth.dependencies import CSRF_COOKIE, require_admin, require_csrf, require_page_admin
from app.employees.models import RecordEmployeeObservationCommand
from app.employees.router import (
    development_api_router,
    development_page_router,
)
from app.main import app, handle_domain_error, include_employee_routers
from app.shared.dependencies import (
    get_auth_service,
    get_employee_service,
    get_settings,
    get_user_service,
)
from app.shared.errors import DomainError
from app.shared.templating import STATIC_DIR
from tests.employee_helpers import EmployeeStack, build_employee_stack
from tests.settings_helpers import make_settings

ORIGIN = "http://testserver"
PASSWORD = "ValidPassword1!"


@pytest.fixture
def employee_stack() -> EmployeeStack:
    return build_employee_stack()


@pytest.fixture
def employee_client(employee_stack: EmployeeStack) -> Iterator[TestClient]:
    app.dependency_overrides[get_auth_service] = lambda: employee_stack.auth.auth_service
    app.dependency_overrides[get_user_service] = lambda: employee_stack.auth.user_service
    app.dependency_overrides[get_employee_service] = lambda: employee_stack.service
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _login(client: TestClient, email: str) -> None:
    response = client.post(
        "/api/v1/auth/login",
        headers={"Origin": ORIGIN},
        json={"email": email, "password": PASSWORD},
    )
    assert response.status_code == 200


def _write_headers(client: TestClient) -> dict[str, str]:
    return {"Origin": ORIGIN, "X-CSRF-Token": client.cookies[CSRF_COOKIE]}


def _employee_payload(stack: EmployeeStack, operation_id: str | None = None) -> dict[str, str]:
    return {
        "employee_no": "EMP-API-001",
        "user_id": stack.staff.id,
        "display_name": "API 가상 직원",
        "department": "플랫폼팀",
        "position": "연구원",
        "office_zone": "A-101",
        "operation_id": operation_id or str(uuid4()),
    }


def test_직원_API_CRUD_목록_이력과_GET_무부작용(
    employee_client: TestClient,
    employee_stack: EmployeeStack,
) -> None:
    _login(employee_client, employee_stack.admin.email)
    created_response = employee_client.post(
        "/api/v1/employees",
        headers=_write_headers(employee_client),
        json=_employee_payload(employee_stack),
    )
    assert created_response.status_code == 201
    created = created_response.json()
    assert created["current_status"]["status"] == "AWAY"
    assert created_response.headers["location"].endswith(created["id"])

    list_response = employee_client.get(
        "/api/v1/employees?search=API&department=플랫폼팀&status=AWAY&limit=1&offset=0"
    )
    assert list_response.status_code == 200
    assert set(list_response.json()) == {"items", "total", "limit", "offset"}
    assert list_response.json()["total"] == 1
    before = employee_stack.employees.get_employee(created["id"])

    for _ in range(3):
        assert employee_client.get(f"/api/v1/employees/{created['id']}").status_code == 200
        history = employee_client.get(
            f"/api/v1/employees/{created['id']}/status-history?source=SYSTEM&to=AWAY"
        )
        assert history.status_code == 200
        assert history.json()["total"] == 1
    assert employee_stack.employees.get_employee(created["id"]) == before

    updated_response = employee_client.patch(
        f"/api/v1/employees/{created['id']}",
        headers=_write_headers(employee_client),
        json={
            "expected_version": created["version"],
            "operation_id": str(uuid4()),
            "display_name": "수정된 API 직원",
        },
    )
    assert updated_response.status_code == 200
    updated = updated_response.json()
    assert updated["display_name"] == "수정된 API 직원"

    deactivated = employee_client.request(
        "DELETE",
        f"/api/v1/employees/{created['id']}",
        headers=_write_headers(employee_client),
        json={
            "expected_version": updated["version"],
            "operation_id": str(uuid4()),
        },
    )
    assert deactivated.status_code == 204
    stored = employee_stack.employees.get_employee(created["id"])
    assert stored is not None
    assert not stored.is_active


def test_생성_mock_WORKING_override_OFFSITE_mock무시_해제_재평가_여정(
    employee_client: TestClient,
    employee_stack: EmployeeStack,
) -> None:
    _login(employee_client, employee_stack.admin.email)
    created = employee_client.post(
        "/api/v1/employees",
        headers=_write_headers(employee_client),
        json=_employee_payload(employee_stack),
    ).json()
    first_seen = employee_stack.auth.clock()
    employee_stack.service.record_mock_observation(
        employee_stack.admin,
        RecordEmployeeObservationCommand(
            event_id=str(uuid4()),
            employee_id=created["id"],
            person_present=True,
            phone_detected=False,
            confidence=0.9,
            observed_at=first_seen,
        ),
    )
    working = employee_client.get(f"/api/v1/employees/{created['id']}").json()
    assert working["current_status"]["status"] == "WORKING"

    override = employee_client.put(
        f"/api/v1/employees/{created['id']}/status-override",
        headers=_write_headers(employee_client),
        json={
            "status": "OFFSITE",
            "reason": "외부 일정",
            "expected_version": working["version"],
            "operation_id": str(uuid4()),
        },
    )
    assert override.status_code == 200
    assert override.json()["current_status"]["status"] == "OFFSITE"

    employee_stack.auth.clock.advance(seconds=30)
    employee_stack.service.record_mock_observation(
        employee_stack.admin,
        RecordEmployeeObservationCommand(
            event_id=str(uuid4()),
            employee_id=created["id"],
            person_present=True,
            phone_detected=True,
            confidence=0.9,
            observed_at=employee_stack.auth.clock(),
        ),
    )
    ignored = employee_client.get(f"/api/v1/employees/{created['id']}").json()
    assert ignored["current_status"]["status"] == "OFFSITE"

    cleared = employee_client.request(
        "DELETE",
        f"/api/v1/employees/{created['id']}/status-override",
        headers=_write_headers(employee_client),
        json={
            "expected_version": ignored["version"],
            "operation_id": str(uuid4()),
        },
    )
    assert cleared.status_code == 200
    assert cleared.json()["current_status"]["status"] == "ON_CALL"


def test_API_인증_권한_CSRF_CAS와_오류_형식(
    employee_client: TestClient,
    employee_stack: EmployeeStack,
) -> None:
    assert employee_client.get("/api/v1/employees").status_code == 401
    _login(employee_client, employee_stack.student.email)
    assert employee_client.get("/api/v1/employees").status_code == 200
    forbidden = employee_client.post(
        "/api/v1/employees",
        headers=_write_headers(employee_client),
        json=_employee_payload(employee_stack),
    )
    assert forbidden.status_code == 403
    evaluation_forbidden = employee_client.post(
        "/api/v1/employee-status-evaluations",
        headers=_write_headers(employee_client),
        json={"operation_id": str(uuid4())},
    )
    assert evaluation_forbidden.status_code == 403

    employee_client.post(
        "/logout",
        headers={"Origin": ORIGIN},
        data={"csrf_token": employee_client.cookies[CSRF_COOKIE]},
    )
    _login(employee_client, employee_stack.admin.email)
    no_csrf = employee_client.post("/api/v1/employees", json=_employee_payload(employee_stack))
    assert no_csrf.status_code == 403
    missing = employee_client.get("/api/v1/employees/not-found")
    assert missing.status_code == 404
    assert set(missing.json()["error"]) == {"code", "message", "details"}

    created = employee_client.post(
        "/api/v1/employees",
        headers=_write_headers(employee_client),
        json=_employee_payload(employee_stack),
    ).json()
    conflict = employee_client.patch(
        f"/api/v1/employees/{created['id']}",
        headers=_write_headers(employee_client),
        json={
            "expected_version": created["version"] + 1,
            "operation_id": str(uuid4()),
            "display_name": "충돌",
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "EMPLOYEE_CONCURRENT_UPDATE"


def test_Jinja2_정상_빈_오류_권한없음_상태(
    employee_client: TestClient,
    employee_stack: EmployeeStack,
) -> None:
    redirect = employee_client.get("/employees", follow_redirects=False)
    assert redirect.status_code == 303
    assert redirect.headers["location"].startswith("/login?")

    _login(employee_client, employee_stack.admin.email)
    empty = employee_client.get("/employees")
    assert empty.status_code == 200
    assert "활성 직원이 없습니다" in empty.text
    created = employee_stack.create_employee()
    normal = employee_client.get("/employees")
    detail = employee_client.get(f"/employees/{created.id}")
    admin = employee_client.get("/admin/employees")
    assert normal.status_code == detail.status_code == admin.status_code == 200
    assert "가상 직원" in normal.text
    assert "현재 상태" in detail.text
    assert "시간 정책 평가" in admin.text
    assert "/admin/dev-tools" not in admin.text

    override_page = employee_client.post(
        f"/employees/{created.id}/status-override",
        headers={"Origin": ORIGIN},
        data={
            "csrf_token": employee_client.cookies[CSRF_COOKIE],
            "operation_id": str(uuid4()),
            "expected_version": str(created.version),
            "status": "OFFSITE",
            "reason": "종료 시각 없는 외부 일정",
            "ends_at": "",
        },
        follow_redirects=False,
    )
    assert override_page.status_code == 303

    evaluation = employee_client.post(
        "/admin/employees/evaluate",
        headers={"Origin": ORIGIN},
        data={
            "csrf_token": employee_client.cookies[CSRF_COOKIE],
            "operation_id": str(uuid4()),
        },
        follow_redirects=False,
    )
    assert evaluation.status_code == 303
    assert evaluation.headers["location"].startswith("/admin/employees?evaluated=1&changed=0")

    duplicate = employee_client.post(
        "/admin/employees",
        headers={"Origin": ORIGIN},
        data={
            "csrf_token": employee_client.cookies[CSRF_COOKIE],
            "operation_id": str(uuid4()),
            "employee_no": created.employee_no,
            "user_id": "",
            "display_name": "중복 직원",
            "department": "플랫폼팀",
            "position": "연구원",
            "office_zone": "A-101",
        },
    )
    assert duplicate.status_code == 409
    assert "이미 사용 중인 직원 번호" in duplicate.text

    employee_client.post(
        "/logout",
        headers={"Origin": ORIGIN},
        data={"csrf_token": employee_client.cookies[CSRF_COOKIE]},
    )
    _login(employee_client, employee_stack.student.email)
    denied = employee_client.get("/admin/employees")
    assert denied.status_code == 403
    assert "권한" in denied.text


def test_mock_HTTP_schema는_service로_전달되고_개발_화면은_빈상태와_오류를_표시한다(
    employee_stack: EmployeeStack,
) -> None:
    development_app = FastAPI()
    development_app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    development_app.include_router(development_api_router)
    development_app.include_router(development_page_router)
    development_app.add_exception_handler(
        DomainError,
        handle_domain_error,  # type: ignore[arg-type]
    )
    development_app.dependency_overrides[require_csrf] = lambda: None
    development_app.dependency_overrides[require_admin] = lambda: employee_stack.admin
    development_app.dependency_overrides[require_page_admin] = lambda: employee_stack.admin
    development_app.dependency_overrides[get_employee_service] = lambda: employee_stack.service
    development_app.dependency_overrides[get_settings] = lambda: make_settings(_env_file=None)

    with TestClient(development_app) as client:
        empty = client.get("/admin/dev-tools")
        assert empty.status_code == 200
        assert "활성 직원이 없습니다" in empty.text
        employee = employee_stack.create_employee()
        response = client.post(
            "/api/v1/mock-employee-observations",
            json={
                "event_id": str(uuid4()),
                "employee_id": employee.id,
                "person_present": True,
                "phone_detected": False,
                "confidence": 0.88,
                "observed_at": "2026-08-05T18:00:00+09:00",
            },
        )
        assert response.status_code == 200
        assert response.json()["resulting_status"] == "WORKING"
        assert response.json()["observed_at"].endswith("Z")

        current = employee_stack.employees.get_employee(employee.id)
        assert current is not None
        employee_stack.service.deactivate_employee(
            employee_stack.admin,
            employee.id,
            expected_version=current.version,
            operation_id=str(uuid4()),
            ip_fingerprint="test-ip-fingerprint",
        )
        error = client.post(
            "/admin/dev-tools",
            data={
                "csrf_token": "test-csrf",
                "event_id": str(uuid4()),
                "employee_id": employee.id,
                "person_present": "true",
                "phone_detected": "false",
                "confidence": "0.9",
                "observed_at": employee_stack.auth.clock().isoformat(),
            },
        )
        assert error.status_code == 409
        assert "비활성 직원" in error.text


def test_production에는_mock_router가_없고_평가_endpoint는_있다() -> None:
    production = make_settings(
        _env_file=None,
        app_env="prod",
        database_mode="mongodb",
        database_url="mongodb://example.invalid",
        database_name="smart_office",
        mock_inputs_enabled=False,
    )
    production_app = FastAPI()
    include_employee_routers(production_app, production)
    paths = set(production_app.openapi()["paths"])

    assert "/api/v1/mock-employee-observations" not in paths
    assert "/admin/dev-tools" not in paths
    assert "/api/v1/employee-status-evaluations" in paths

    development = make_settings(_env_file=None, mock_inputs_enabled=True)
    development_app = FastAPI()
    include_employee_routers(development_app, development)
    development_paths = set(development_app.openapi()["paths"])
    assert "/api/v1/mock-employee-observations" in development_paths
    assert "/admin/dev-tools" in development_paths


def test_OpenAPI에_직원_요청응답과_표준_오류_contract가_노출된다(
    employee_client: TestClient,
) -> None:
    schema = employee_client.get("/openapi.json").json()
    assert "/api/v1/employees" in schema["paths"]
    assert "/api/v1/employees/{employee_id}/status-history" in schema["paths"]
    assert "/api/v1/employee-status-evaluations" in schema["paths"]
    assert "/api/v1/mock-employee-observations" not in schema["paths"]
    responses = schema["paths"]["/api/v1/employees"]["post"]["responses"]
    assert responses["201"]["content"]["application/json"]["schema"]
    assert responses["409"]["content"]["application/json"]["schema"]
