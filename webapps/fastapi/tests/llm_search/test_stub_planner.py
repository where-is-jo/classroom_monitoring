"""LLM 없이 도는 기본 모드의 계약.

대역이 내는 계획도 **실제 모델에게 요구하는 형식과 같아야** 한다. 대역만 통과하는
형식을 쓰면 계약이 검증되지 않는다.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.llm_search.adapters.stub_planner import StubQueryPlanner
from app.llm_search.planning import parse_plan
from app.llm_search.ports import PlanPrompt


def _plan(now: datetime) -> str:
    return StubQueryPlanner().plan(PlanPrompt(system="", question="아무 질문", now=now))


def test_질문과_무관하게_오늘_하루를_돌려준다() -> None:
    """자연어 해석을 흉내 내면 검색 규칙이 두 벌이 된다."""
    query = parse_plan(
        _plan(datetime(2026, 8, 14, 10, 0, tzinfo=UTC)), max_span_days=7, limit_ceiling=20
    )

    # KST 2026-08-14 00:00 ~ 08-15 00:00 = UTC 08-13 15:00 ~ 08-14 15:00
    assert query.from_at == datetime(2026, 8, 13, 15, 0, tzinfo=UTC)
    assert query.to_at == datetime(2026, 8, 14, 15, 0, tzinfo=UTC)
    assert query.camera_id is None
    assert query.classroom_id is None


def test_하루의_경계를_한국_시각으로_자른다() -> None:
    """UTC로 자르면 오전 9시에 날짜가 바뀌어 '오늘'이 어제 오전까지를 포함한다."""
    # UTC 00:30은 KST로 같은 날 09:30이다. 하루의 시작은 그 전날 UTC 15:00이어야 한다.
    query = parse_plan(
        _plan(datetime(2026, 8, 14, 0, 30, tzinfo=UTC)), max_span_days=7, limit_ceiling=20
    )

    assert query.from_at == datetime(2026, 8, 13, 15, 0, tzinfo=UTC)


def test_모델에게_요구하는_것과_같은_시각_형식을_쓴다() -> None:
    """대역만 UTC로 답하면 "모델이 낸 값이 UTC로 정규화되는가"가 검증되지 않는다."""
    raw = _plan(datetime(2026, 8, 14, 10, 0, tzinfo=UTC))

    assert "+09:00" in raw
    assert "Z" not in raw


def test_요청한_limit을_그대로_쓴다() -> None:
    """대역은 limit을 정하지 않는다. 요청값이 그대로 살아야 한다."""
    query = parse_plan(
        _plan(datetime(2026, 8, 14, 10, 0, tzinfo=UTC)), max_span_days=7, limit_ceiling=7
    )

    assert query.limit == 7
