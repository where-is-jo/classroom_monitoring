"""모델 원문 → 검증된 검색 조건.

여기가 이 기능의 방어선이라 **정상 케이스보다 실패 케이스가 많다.** 실제 모델을
띄우지 않고도 "이상한 응답이 왔을 때 무슨 일이 일어나는가"를 전부 고정한다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.llm_search.errors import LlmSearchPlanInvalidError
from app.llm_search.planning import MAX_LIMIT, MAX_PLAN_TEXT_BYTES, parse_plan

_VALID = {
    "intent": "detection_search",
    "camera_id": None,
    "classroom_id": "A101",
    "from": "2026-08-14T06:00:00Z",
    "to": "2026-08-14T07:00:00Z",
    "limit": 20,
}


def _parse(text: str, *, max_span_days: int = 7, default_limit: int = 20) -> object:
    return parse_plan(text, max_span_days=max_span_days, default_limit=default_limit)


def _reason(text: str, *, max_span_days: int = 7) -> str:
    with pytest.raises(LlmSearchPlanInvalidError) as error:
        _parse(text, max_span_days=max_span_days)
    return str(error.value.details["reason"])


def test_규격에_맞는_응답을_검색_조건으로_바꾼다() -> None:
    query = parse_plan(json.dumps(_VALID), max_span_days=7, default_limit=20)

    assert query.camera_id is None
    assert query.classroom_id == "A101"
    assert query.from_at == datetime(2026, 8, 14, 6, 0, tzinfo=UTC)
    assert query.to_at == datetime(2026, 8, 14, 7, 0, tzinfo=UTC)
    assert query.limit == 20
    assert query.notes == ()


def test_코드펜스로_감싼_응답도_읽는다() -> None:
    text = f"```json\n{json.dumps(_VALID)}\n```"

    query = parse_plan(text, max_span_days=7, default_limit=20)

    assert query.classroom_id == "A101"


def test_앞뒤에_설명이_붙어도_읽는다() -> None:
    text = f"네, 아래와 같이 변환했습니다.\n{json.dumps(_VALID)}\n필요하면 알려주세요."

    query = parse_plan(text, max_span_days=7, default_limit=20)

    assert query.classroom_id == "A101"


def test_시각대가_다른_표기도_UTC로_맞춘다() -> None:
    payload = {**_VALID, "from": "2026-08-14T15:00:00+09:00", "to": "2026-08-14T16:00:00+09:00"}

    query = parse_plan(json.dumps(payload), max_span_days=7, default_limit=20)

    assert query.from_at == datetime(2026, 8, 14, 6, 0, tzinfo=UTC)


def test_빈_문자열_식별자는_지정하지_않은_것으로_본다() -> None:
    payload = {**_VALID, "classroom_id": "   "}

    query = parse_plan(json.dumps(payload), max_span_days=7, default_limit=20)

    assert query.classroom_id is None


def test_최상위가_객체가_아니면_거부한다() -> None:
    """`[{...}]`도 유효한 JSON이다. 안쪽 객체를 주워 쓰면 규격 위반이 조용히 통과한다."""
    assert _reason(json.dumps([_VALID])) == "NOT_OBJECT"
    assert _reason('"detection_search"') == "NOT_OBJECT"


def test_JSON이_아니면_거부한다() -> None:
    assert _reason("죄송합니다. 무슨 말인지 모르겠습니다.") == "NOT_JSON"
    assert _reason("") == "NOT_JSON"
    assert _reason("{intent: detection_search}") == "NOT_JSON"


def test_모르는_키가_있으면_거부한다() -> None:
    """모델이 지어낸 키가 저장소 질의로 흘러가지 않게 한다."""
    payload = {**_VALID, "$where": "1==1"}

    assert _reason(json.dumps(payload)) == "UNKNOWN_FIELD"


def test_오류_본문에_모델_원문을_담지_않는다() -> None:
    payload = {**_VALID, "비밀키": "내부 정보"}

    with pytest.raises(LlmSearchPlanInvalidError) as error:
        _parse(json.dumps(payload))

    body = json.dumps(
        {"message": error.value.message, "details": error.value.details}, ensure_ascii=False
    )
    assert "비밀키" not in body
    assert "내부 정보" not in body


def test_지원하지_않는_intent를_거부한다() -> None:
    assert _reason(json.dumps({**_VALID, "intent": "delete_events"})) == "UNSUPPORTED_INTENT"
    payload = {key: value for key, value in _VALID.items() if key != "intent"}
    assert _reason(json.dumps(payload)) == "UNSUPPORTED_INTENT"


def test_기간이_없으면_거부한다() -> None:
    payload = {key: value for key, value in _VALID.items() if key != "from"}

    assert _reason(json.dumps(payload)) == "MISSING_FIELD"


def test_시각대_없는_시각을_거부한다() -> None:
    """UTC로 가정해 버리면 9시간 어긋난 결과를 정상처럼 돌려주게 된다."""
    payload = {**_VALID, "from": "2026-08-14T06:00:00"}

    assert _reason(json.dumps(payload)) == "NAIVE_DATETIME"


def test_시각_형식이_틀리면_거부한다() -> None:
    assert _reason(json.dumps({**_VALID, "to": "오늘 저녁"})) == "INVALID_DATETIME"


@pytest.mark.parametrize(
    ("from_at", "to_at"),
    [
        ("2026-08-14T06:00:00Z", "2026-08-14T06:00:00Z"),
        ("2026-08-14T07:00:00Z", "2026-08-14T06:00:00Z"),
    ],
)
def test_시작이_끝보다_뒤이거나_같으면_거부한다(from_at: str, to_at: str) -> None:
    """저장소가 반개구간이라 두 값이 같으면 언제나 0건이다. 빈 결과와 구분한다."""
    payload = {**_VALID, "from": from_at, "to": to_at}

    assert _reason(json.dumps(payload)) == "EMPTY_RANGE"


def test_타입이_틀린_값을_거부한다() -> None:
    assert _reason(json.dumps({**_VALID, "classroom_id": 101})) == "INVALID_TYPE"
    assert _reason(json.dumps({**_VALID, "from": 1755151200})) == "INVALID_TYPE"
    assert _reason(json.dumps({**_VALID, "limit": "20"})) == "INVALID_TYPE"


def test_limit이_참거짓이면_거부한다() -> None:
    """bool은 int의 하위 타입이라 그냥 두면 true가 1건으로 조용히 통과한다."""
    assert _reason(json.dumps({**_VALID, "limit": True})) == "INVALID_TYPE"


def test_식별자가_지나치게_길면_거부한다() -> None:
    payload = {**_VALID, "classroom_id": "A" * 129}

    assert _reason(json.dumps(payload)) == "IDENTIFIER_TOO_LONG"


def test_응답이_지나치게_크면_읽지_않는다() -> None:
    text = json.dumps({**_VALID, "classroom_id": "A"}) + " " * MAX_PLAN_TEXT_BYTES

    assert _reason(text) == "PLAN_TOO_LARGE"


def test_기간_상한을_넘으면_거절하지_않고_줄인_뒤_알린다() -> None:
    """'이번 달'은 일상적인 질문이다. 422로 돌려주면 쓸 수 없다."""
    payload = {**_VALID, "from": "2026-07-01T00:00:00Z", "to": "2026-08-14T00:00:00Z"}

    query = parse_plan(json.dumps(payload), max_span_days=7, default_limit=20)

    assert query.to_at == datetime(2026, 8, 14, 0, 0, tzinfo=UTC)
    assert query.from_at == datetime(2026, 8, 7, 0, 0, tzinfo=UTC)
    assert query.notes == ("조회 기간이 너무 길어 마지막 7일만 찾았습니다.",)


def test_limit이_상한을_넘으면_줄인_뒤_알린다() -> None:
    payload = {**_VALID, "limit": 100000}

    query = parse_plan(json.dumps(payload), max_span_days=7, default_limit=20)

    assert query.limit == MAX_LIMIT
    assert len(query.notes) == 1


def test_limit이_없으면_요청값을_쓴다() -> None:
    payload = {key: value for key, value in _VALID.items() if key != "limit"}

    query = parse_plan(json.dumps(payload), max_span_days=7, default_limit=5)

    assert query.limit == 5
    assert query.notes == ()
