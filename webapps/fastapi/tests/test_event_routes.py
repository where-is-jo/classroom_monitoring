"""화면과 API 라우트 테스트.

같은 서비스를 쓰는 두 경로가 모두 동작하는지 확인한다.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.shared.dependencies import get_event_repository

from .conftest import FakeEventRepository


def test_헬스체크는_200을_반환한다(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_루트는_이벤트_목록으로_리다이렉트한다(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/events"


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


def test_API_목록은_페이지네이션_필드를_포함한다(client: TestClient) -> None:
    response = client.get("/api/v1/events?limit=2&offset=1")

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2
    assert body["total"] == 5
    assert body["limit"] == 2
    assert body["offset"] == 1


def test_API_목록의_limit은_상한을_넘지_않는다(client: TestClient) -> None:
    response = client.get("/api/v1/events?limit=9999")

    assert response.status_code == 200
    assert response.json()["limit"] == 200


def test_API_상세는_ISO8601_UTC로_시각을_반환한다(client: TestClient) -> None:
    response = client.get("/api/v1/events/evt-test-001")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "evt-test-001"
    assert body["detected_at"] == "2026-08-05T09:00:00Z"
    assert body["confidence_level"] == "high"


def test_없는_이벤트_API는_404와_오류_본문을_반환한다(client: TestClient) -> None:
    response = client.get("/api/v1/events/evt-does-not-exist")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "EVENT_NOT_FOUND"
    assert error["message"]
    # 내부 정보가 새지 않는지 확인한다.
    assert "Traceback" not in response.text


# --------------------------------------------------------------------------
# 화면
# --------------------------------------------------------------------------


def test_목록_화면은_이벤트_행을_렌더링한다(client: TestClient) -> None:
    response = client.get("/events")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "evt-test-001" in response.text
    assert "탐지 이벤트" in response.text


def test_목록_화면은_비어_있으면_빈_상태를_보여준다(client: TestClient) -> None:
    """조회 실패가 아니라 데이터 없음임을 구분해 표시해야 한다."""
    # client.app은 ASGIApp으로 타입이 좁혀져 dependency_overrides를 갖지 않는다.
    # 같은 객체이므로 app을 직접 쓴다. 정리는 client 픽스처가 한다.
    app.dependency_overrides[get_event_repository] = lambda: FakeEventRepository([])

    response = client.get("/events")

    assert response.status_code == 200
    assert "표시할 탐지 이벤트가 없습니다" in response.text


def test_상세_화면은_이벤트_정보를_보여준다(client: TestClient) -> None:
    response = client.get("/events/evt-test-001")

    assert response.status_code == 200
    assert "evt-test-001" in response.text
    assert "cam-test-01" in response.text


def test_없는_이벤트_화면은_404_페이지를_보여준다(client: TestClient) -> None:
    response = client.get("/events/evt-does-not-exist")

    assert response.status_code == 404
    assert "text/html" in response.headers["content-type"]
    assert "찾을 수 없습니다" in response.text
