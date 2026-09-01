"""자연어 검색 계측의 계약. LLM도 HTTP도 없이 돈다.

지표는 전역 레지스트리에 누적되므로 **절대값을 단정하지 않고 증분만 본다.**
테스트 실행 순서에 따라 시작값이 달라진다.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from app.llm_search.errors import LlmSearchPlanInvalidError, LlmSearchPlannerUnavailableError
from app.llm_search.metrics import PlanOutcome
from app.main import app
from app.shared.config import Settings
from app.shared.metrics import METRIC_PREFIX

from .test_llama_planner import _PROMPT, _response, _sequence_planner
from .test_service import _FROM, _TO, FakePlanner, SequencePlanner, _build, _event

_VALID_PLAN = json.dumps(
    {
        "intent": "detection_search",
        "camera_id": None,
        "classroom_id": None,
        "from": _FROM,
        "to": _TO,
        "limit": 20,
    }
)


def value(name: str, **labels: str) -> float:
    """전역 레지스트리에서 지표 값을 읽는다. 아직 없으면 0으로 본다."""
    sampled = REGISTRY.get_sample_value(f"{METRIC_PREFIX}{name}", labels or None)
    return 0.0 if sampled is None else float(sampled)


def plan_attempts(*, attempt: str, outcome: PlanOutcome) -> float:
    return value("llm_plan_duration_seconds_count", attempt=attempt, outcome=outcome)


def searches(*, outcome: PlanOutcome) -> float:
    return value("llm_search_duration_seconds_count", outcome=outcome)


def test_성공한_검색을_한_번_기록한다() -> None:
    before_plan = plan_attempts(attempt="first", outcome="success")
    before_search = searches(outcome="success")

    _build(events=[_event("camera-01", 0, 1)]).search("오늘 누가 왔어?", limit=20)

    assert plan_attempts(attempt="first", outcome="success") == before_plan + 1
    assert searches(outcome="success") == before_search + 1


def test_재시도를_첫_시도와_나눠_센다() -> None:
    """첫 시도 실패율이 올라가도 사용자에게는 '조금 느리네'로만 보인다."""
    before_first = plan_attempts(attempt="first", outcome="invalid")
    before_retry = plan_attempts(attempt="retry", outcome="success")
    planner = SequencePlanner("무슨 말인지 모르겠습니다.", _VALID_PLAN)

    _build(planner=planner).search("오늘", limit=20)

    assert plan_attempts(attempt="first", outcome="invalid") == before_first + 1
    assert plan_attempts(attempt="retry", outcome="success") == before_retry + 1


def test_닿지_못한_경우는_재시도로_세지_않는다() -> None:
    """서버가 죽은 상황에서 한 번 더 부르면 사용자를 두 배로 기다리게 할 뿐이다."""
    before_first = plan_attempts(attempt="first", outcome="unavailable")
    before_retry = plan_attempts(attempt="retry", outcome="unavailable")
    before_search = searches(outcome="unavailable")

    with pytest.raises(LlmSearchPlannerUnavailableError):
        _build(planner=FakePlanner(fails=True)).search("오늘", limit=20)

    assert plan_attempts(attempt="first", outcome="unavailable") == before_first + 1
    assert plan_attempts(attempt="retry", outcome="unavailable") == before_retry
    assert searches(outcome="unavailable") == before_search + 1


def test_두_번_다_규격을_벗어나면_검색을_규격_위반으로_남긴다() -> None:
    before = searches(outcome="invalid")

    with pytest.raises(LlmSearchPlanInvalidError):
        _build(planner=FakePlanner(None)).search("오늘", limit=20)

    assert searches(outcome="invalid") == before + 1


def test_실패한_검색도_지연_분포에_남긴다() -> None:
    """사용자는 실패한 검색도 똑같이 기다렸다."""
    before = value("llm_search_duration_seconds_sum", outcome="unavailable")

    with pytest.raises(LlmSearchPlannerUnavailableError):
        _build(planner=FakePlanner(fails=True)).search("오늘", limit=20)

    assert value("llm_search_duration_seconds_sum", outcome="unavailable") >= before


def test_조회_상한에_걸리면_따로_센다() -> None:
    """계속 늘면 SCAN_LIMIT이 실제 이벤트 양에 비해 작다는 신호다."""
    before = value("llm_search_truncated_total")
    events = [_event("camera-01", minute, minute % 2) for minute in range(6)]

    outcome = _build(events=events, scan_limit=3).search("오늘", limit=20)

    assert outcome.truncated is True
    assert value("llm_search_truncated_total") == before + 1


def test_잘리지_않은_검색은_세지_않는다() -> None:
    before = value("llm_search_truncated_total")

    outcome = _build(events=[_event("camera-01", 0, 1)]).search("오늘", limit=20)

    assert outcome.truncated is False
    assert value("llm_search_truncated_total") == before


def test_스키마_폴백_횟수를_센다() -> None:
    """상시 발동 중이면 생성 단계에서 구조를 강제하지 못하고 있다는 뜻이다."""
    before = value("llm_schema_fallback_total")
    planner, _ = _sequence_planner(
        _response({"error": "unsupported response_format"}, status=400),
        _response({"choices": [{"message": {"content": _VALID_PLAN}}]}),
    )

    planner.plan(_PROMPT)

    assert value("llm_schema_fallback_total") == before + 1


def test_폴백하지_않으면_세지_않는다() -> None:
    before = value("llm_schema_fallback_total")
    planner, _ = _sequence_planner(_response({"choices": [{"message": {"content": _VALID_PLAN}}]}))

    planner.plan(_PROMPT)

    assert value("llm_schema_fallback_total") == before


def test_metrics_경로가_지표를_돌려준다() -> None:
    with TestClient(app) as client:
        response = client.get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert f"{METRIC_PREFIX}llm_search_duration_seconds" in response.text


def test_metrics_경로는_API_문서에_넣지_않는다() -> None:
    """제품 API가 아니라 운영용 경로다."""
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()

    assert "/metrics" not in schema["paths"]


def test_지표_노출_기본값은_켜짐이다() -> None:
    """`/health`와 자동 생성 문서가 이미 같은 포트로 공개돼 새로운 종류의 노출이 아니다."""
    assert Settings(_env_file=None).metrics_enabled is True  # type: ignore[call-arg]


def test_지표_노출을_끌_수_있다() -> None:
    """라우트를 import 시점에 등록하므로, 값을 바꾸면 앱을 다시 띄워야 반영된다."""
    settings = Settings(_env_file=None, metrics_enabled=False)  # type: ignore[call-arg]

    assert settings.metrics_enabled is False
