"""검색 API와 화면의 계약.

화면이 구분해야 하는 상태가 다섯이다 — 질문 전 / 결과 없음 / LLM에 닿지 못함 /
조건으로 바꾸지 못함 / 이미지 확인 실패. **묶어서 보여주면 사용자가 무엇을 해야
하는지 알 수 없다.** 여기서 다섯을 모두 고정한다.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.llm_search.errors import LlmSearchPlanInvalidError, LlmSearchPlannerUnavailableError
from app.llm_search.models import DetectionHit, IdentifiedStudent, SearchOutcome, SearchQuery
from app.main import app
from app.shared.dependencies import get_llm_search_service

_QUERY = SearchQuery(
    camera_id=None,
    classroom_id="A101",
    from_at=datetime(2026, 8, 14, 0, 0, tzinfo=UTC),
    to_at=datetime(2026, 8, 15, 0, 0, tzinfo=UTC),
    limit=20,
    notes=("조회 기간이 너무 길어 마지막 7일만 찾았습니다.",),
)
_HIT = DetectionHit(
    event_id="event-1",
    camera_id="camera-01",
    resolved_classroom_id="A101",
    captured_at=datetime(2026, 8, 14, 6, 30, tzinfo=UTC),
    detection_count=2,
    identified=(IdentifiedStudent(student_id="student-1", identity_confidence=0.9),),
    unidentified_count=1,
    snapshot_key="camera-01/2026-08-14/20260814T063000Z.jpg",
)


class FakeService:
    def __init__(
        self, outcome: SearchOutcome | None = None, *, error: Exception | None = None
    ) -> None:
        self._outcome = outcome
        self._error = error

    def search(self, question: str, *, limit: int) -> SearchOutcome:
        if self._error is not None:
            raise self._error
        assert self._outcome is not None
        return self._outcome


def _outcome(
    *, hits: tuple[DetectionHit, ...] = (), truncated: bool = False, snapshot_failed: bool = False
) -> SearchOutcome:
    return SearchOutcome(
        query=_QUERY,
        hits=hits,
        truncated=truncated,
        snapshot_lookup_failed=snapshot_failed,
    )


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_llm_search_service, None)


def _override(service: FakeService) -> None:
    app.dependency_overrides[get_llm_search_service] = lambda: service


def test_검색_결과와_해석한_계획을_함께_돌려준다(client: TestClient) -> None:
    """왜 이 결과가 나왔는지 볼 수 없으면 자연어 검색을 신뢰할 수 없다."""
    _override(FakeService(_outcome(hits=(_HIT,))))

    response = client.post("/api/v1/llm-searches", json={"question": "오늘 A101"})

    assert response.status_code == 200
    body = response.json()
    assert body["plan"]["classroom_id"] == "A101"
    assert body["plan"]["from"] == "2026-08-14T00:00:00Z"
    assert body["plan"]["notes"] == ["조회 기간이 너무 길어 마지막 7일만 찾았습니다."]
    assert body["total"] == 1
    assert body["items"][0]["image_path"] == (
        "/api/v1/snapshots/image/camera-01/2026-08-14/20260814T063000Z.jpg"
    )
    assert body["items"][0]["identified"] == [
        {"student_id": "student-1", "identity_confidence": 0.9}
    ]
    assert body["items"][0]["unidentified_count"] == 1


def test_스냅샷이_없으면_이미지_경로도_없다(client: TestClient) -> None:
    hit = DetectionHit(
        event_id="event-2",
        camera_id="camera-01",
        resolved_classroom_id="A101",
        captured_at=datetime(2026, 8, 14, 7, 0, tzinfo=UTC),
        detection_count=0,
        identified=(),
        unidentified_count=0,
        snapshot_key=None,
    )
    _override(FakeService(_outcome(hits=(hit,))))

    body = client.post("/api/v1/llm-searches", json={"question": "오늘"}).json()

    assert body["items"][0]["snapshot_key"] is None
    assert body["items"][0]["image_path"] is None


def test_결과가_없어도_200이다(client: TestClient) -> None:
    """'그런 기록 없음'은 정상 응답이다. 오류가 아니다."""
    _override(FakeService(_outcome()))

    response = client.post("/api/v1/llm-searches", json={"question": "내일"})

    assert response.status_code == 200
    assert response.json()["total"] == 0


def test_LLM에_닿지_못하면_503이다(client: TestClient) -> None:
    _override(FakeService(error=LlmSearchPlannerUnavailableError()))

    response = client.post("/api/v1/llm-searches", json={"question": "오늘"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "LLM_SEARCH_PLANNER_UNAVAILABLE"


def test_조건으로_바꾸지_못하면_422다(client: TestClient) -> None:
    _override(FakeService(error=LlmSearchPlanInvalidError("NOT_JSON")))

    response = client.post("/api/v1/llm-searches", json={"question": "음"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "LLM_SEARCH_PLAN_INVALID"


def test_모르는_필드가_있는_요청을_거부한다(client: TestClient) -> None:
    """요청에 from/to를 두지 않는다. 계획의 출처가 둘이 되면 우선순위를 정해야 한다."""
    _override(FakeService(_outcome()))

    response = client.post(
        "/api/v1/llm-searches",
        json={"question": "오늘", "from": "2026-08-14T00:00:00Z"},
    )

    assert response.status_code == 422


def test_빈_질문을_거부한다(client: TestClient) -> None:
    _override(FakeService(_outcome()))

    assert client.post("/api/v1/llm-searches", json={"question": ""}).status_code == 422


def test_화면은_질문_전에_안내를_보여준다(client: TestClient) -> None:
    _override(FakeService(_outcome()))

    response = client.get("/llm-search")

    assert response.status_code == 200
    assert "질문을 입력하세요" in response.text


def test_화면이_결과_없음과_조회_실패를_구분한다(client: TestClient) -> None:
    _override(FakeService(_outcome()))
    empty = client.get("/llm-search", params={"q": "오늘"})

    _override(FakeService(error=LlmSearchPlannerUnavailableError()))
    unavailable = client.get("/llm-search", params={"q": "오늘"})

    assert "탐지 기록이 없습니다" in empty.text
    assert "검색 서버에 닿지 못했습니다" in unavailable.text
    assert "탐지 기록이 없습니다" not in unavailable.text


def test_화면이_계획_오류를_따로_보여준다(client: TestClient) -> None:
    """질문을 고치면 되는 상황과 그렇지 않은 상황을 구분한다."""
    _override(FakeService(error=LlmSearchPlanInvalidError("NOT_JSON")))

    response = client.get("/llm-search", params={"q": "음"})

    assert response.status_code == 200
    assert "검색 조건으로 바꾸지 못했습니다" in response.text


def test_화면이_이미지_확인_실패를_결과와_함께_알린다(client: TestClient) -> None:
    """이미지를 못 붙였을 뿐 메타데이터는 유효하다. 둘을 함께 잃으면 안 된다."""
    hit = DetectionHit(
        event_id="event-3",
        camera_id="camera-01",
        resolved_classroom_id="A101",
        captured_at=datetime(2026, 8, 14, 8, 0, tzinfo=UTC),
        detection_count=1,
        identified=(),
        unidentified_count=1,
        snapshot_key=None,
    )
    _override(FakeService(_outcome(hits=(hit,), snapshot_failed=True)))

    response = client.get("/llm-search", params={"q": "오늘"})

    assert "확인하지 못했다는 뜻입니다" in response.text
    assert "이미지 확인 실패" in response.text
    assert "식별 미연동" in response.text


def test_화면이_해석한_계획과_조정_사유를_보여준다(client: TestClient) -> None:
    _override(FakeService(_outcome(hits=(_HIT,), truncated=True)))

    response = client.get("/llm-search", params={"q": "이번 달 A101"})

    assert "이렇게 이해했습니다" in response.text
    assert "마지막 7일만 찾았습니다" in response.text
    assert "이것이 전부가 아닙니다" in response.text
