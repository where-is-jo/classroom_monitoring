"""health/readiness와 공통 오류 응답·오류 화면 테스트."""

from __future__ import annotations

from typing import cast

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from pymongo.errors import ServerSelectionTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.events.models import Event
from app.main import handle_http_error
from app.shared import dependencies
from app.shared.config import Settings
from app.shared.dependencies import get_event_repository, get_settings
from app.shared.errors import RepositoryUnavailableError
from app.shared.templating import STATIC_DIR
from tests.settings_helpers import make_settings


def _application(client: TestClient) -> FastAPI:
    return cast(FastAPI, client.app)


class FailingEventRepository:
    def list_events(self, *, limit: int, offset: int) -> tuple[list[Event], int]:
        raise RepositoryUnavailableError()

    def get_event(self, event_id: str) -> Event | None:
        raise RepositoryUnavailableError()


class SuccessfulPingDatabase:
    def command(self, command_name: str) -> dict[str, int]:
        assert command_name == "ping"
        return {"ok": 1}


class FailingPingDatabase:
    def command(self, command_name: str) -> None:
        assert command_name == "ping"
        raise ServerSelectionTimeoutError(
            "mongodb://credential-marker@internal-mongodb.invalid unavailable"
        )


def mongo_settings() -> Settings:
    return make_settings(
        _env_file=None,
        app_env="dev",
        database_mode="mongodb",
        database_url="mongodb://example.invalid",
        database_name="smart_office",
    )


def test_memory_mode_readiness는_준비_응답을_반환한다(client: TestClient) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_mongodb_readiness_ping_성공을_반환한다(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _application(client).dependency_overrides[get_settings] = mongo_settings
    monkeypatch.setattr(dependencies, "_mongo_database", lambda: SuccessfulPingDatabase())

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_mongodb_readiness_실패는_주소_없는_503_오류를_반환한다(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _application(client).dependency_overrides[get_settings] = mongo_settings
    monkeypatch.setattr(dependencies, "_mongo_database", lambda: FailingPingDatabase())

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "DATABASE_UNAVAILABLE",
            "message": "데이터 저장소 준비 상태를 확인할 수 없습니다.",
            "details": {},
        }
    }
    assert "credential-marker" not in response.text
    assert "internal-mongodb" not in response.text


def test_API_검증_오류도_공통_envelope를_사용한다(client: TestClient) -> None:
    response = client.get("/api/v1/events?limit=not-a-number")

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"]["errors"][0]["location"][-1] == "limit"
    assert "not-a-number" not in response.text


def test_없는_API_경로도_공통_envelope를_사용한다(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "NOT_FOUND",
        "message": "요청한 페이지를 찾을 수 없습니다.",
        "details": {},
    }


def test_권한_없음은_API와_화면에서_명시적으로_표시된다() -> None:
    isolated_app = FastAPI()
    isolated_app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    isolated_app.add_exception_handler(
        StarletteHTTPException,
        handle_http_error,  # type: ignore[arg-type]
    )

    @isolated_app.get("/api/v1/forbidden")
    def forbidden_api() -> None:
        raise HTTPException(status_code=403)

    @isolated_app.get("/forbidden")
    def forbidden_page() -> None:
        raise HTTPException(status_code=403)

    with TestClient(isolated_app) as isolated_client:
        api_response = isolated_client.get("/api/v1/forbidden")
        page_response = isolated_client.get("/forbidden")

    assert api_response.status_code == 403
    assert api_response.json()["error"]["code"] == "FORBIDDEN"
    assert page_response.status_code == 403
    assert "권한이 없습니다" in page_response.text


def test_저장소_오류는_API에서_503_envelope로_표시된다(client: TestClient) -> None:
    _application(client).dependency_overrides[get_event_repository] = FailingEventRepository

    response = client.get("/api/v1/events")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "REPOSITORY_UNAVAILABLE"


def test_저장소_오류는_화면에서_빈_상태와_구분된다(client: TestClient) -> None:
    _application(client).dependency_overrides[get_event_repository] = FailingEventRepository

    response = client.get("/events")

    assert response.status_code == 503
    assert "요청을 처리할 수 없습니다" in response.text
    assert "표시할 탐지 이벤트가 없습니다" not in response.text


def test_OpenAPI와_docs_경로를_유지한다(client: TestClient) -> None:
    openapi_response = client.get("/openapi.json")
    assert openapi_response.status_code == 200
    assert client.get("/docs").status_code == 200
    paths = openapi_response.json()["paths"]
    assert openapi_response.json()["info"]["version"] == "0.2.0"
    assert "/login" not in paths
    assert "/admin" not in paths
    assert "/api/v1/events" not in paths
    assert "/api/v1/events/{event_id}" not in paths
    assert "/api/v1/admin/audit-logs" not in paths
    activity_responses = paths["/api/v1/admin/dashboard-activities"]["get"]["responses"]
    assert activity_responses["422"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }


def test_설계_명세는_별도_Swagger_경로로_제공된다(client: TestClient) -> None:
    spec_response = client.get("/api-spec.json")
    assert spec_response.status_code == 200

    spec = spec_response.json()
    assert spec["openapi"].startswith("3.1")
    assert "/api/v1/dashboard/summary" in spec["paths"]
    assert "/internal/v1/detections" in spec["paths"]

    docs_response = client.get("/docs/api-spec")
    assert docs_response.status_code == 200
    assert "/api-spec.json" in docs_response.text


def test_설계_명세는_앱_OpenAPI_스키마를_오염시키지_않는다(client: TestClient) -> None:
    """구현되지 않은 경로가 실제 API 문서에 있는 기능처럼 보이면 안 된다."""
    paths = set(client.get("/openapi.json").json()["paths"])

    assert "/api/v1/dashboard/summary" not in paths
    assert "/api/v1/employee-statuses" not in paths
    assert "/api-spec.json" not in paths
    assert "/docs/api-spec" not in paths


def test_설계_명세의_모든_참조가_실제로_존재한다(client: TestClient) -> None:
    """$ref 오타는 Swagger UI에서 조용히 빈 스키마로 보인다. 문서 단계에서 잡는다."""
    spec = client.get("/api-spec.json").json()

    def collect_refs(node: object) -> list[str]:
        if isinstance(node, dict):
            found = [node["$ref"]] if isinstance(node.get("$ref"), str) else []
            for key, value in node.items():
                if key != "$ref":
                    found.extend(collect_refs(value))
            return found
        if isinstance(node, list):
            return [ref for item in node for ref in collect_refs(item)]
        return []

    for ref in collect_refs(spec):
        assert ref.startswith("#/"), ref
        target: object = spec
        for segment in ref.removeprefix("#/").split("/"):
            assert isinstance(target, dict) and segment in target, ref
            target = target[segment]


def test_local_memory_mode_기반_사용자_여정(client: TestClient) -> None:
    """기동→준비 확인→기존 API→기존 화면 흐름이 외부 서비스 없이 동작한다."""
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/health/ready").json() == {"status": "ready"}

    event_list = client.get("/api/v1/events?limit=1&offset=0")
    assert event_list.status_code == 200
    assert event_list.json()["limit"] == 1
    assert event_list.headers["deprecation"] == "true"

    event_page = client.get("/events")
    assert event_page.status_code == 200
    assert "탐지 이벤트" in event_page.text
