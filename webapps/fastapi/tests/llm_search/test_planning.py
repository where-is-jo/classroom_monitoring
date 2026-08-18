"""모델 원문 → 검증된 검색 조건.

여기가 이 기능의 방어선이라 **정상 케이스보다 실패 케이스가 많다.** 실제 모델을
띄우지 않고도 "이상한 응답이 왔을 때 무슨 일이 일어나는가"를 전부 고정한다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.llm_search.errors import LlmSearchPlanInvalidError
from app.llm_search.planning import (
    MAX_LIMIT,
    MAX_PLAN_TEXT_BYTES,
    PLAN_JSON_SCHEMA,
    parse_plan,
)

# _VALID의 구간(08-14 06:00~07:00Z)보다 뒤여야 한다. 앞이면 미래 구간이 되어
# 절삭에 걸린다.
_NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)

_VALID = {
    "intent": "detection_search",
    "camera_id": None,
    "classroom_id": "A101",
    "from": "2026-08-14T06:00:00Z",
    "to": "2026-08-14T07:00:00Z",
    "limit": 20,
}


def _parse(
    text: str, *, now: datetime = _NOW, max_span_days: int = 7, limit_ceiling: int = 20
) -> object:
    return parse_plan(text, now=now, max_span_days=max_span_days, limit_ceiling=limit_ceiling)


def _reason(text: str, *, max_span_days: int = 7) -> str:
    with pytest.raises(LlmSearchPlanInvalidError) as error:
        _parse(text, max_span_days=max_span_days)
    return str(error.value.details["reason"])


def test_규격에_맞는_응답을_검색_조건으로_바꾼다() -> None:
    query = parse_plan(json.dumps(_VALID), now=_NOW, max_span_days=7, limit_ceiling=20)

    assert query.camera_id is None
    assert query.classroom_id == "A101"
    assert query.from_at == datetime(2026, 8, 14, 6, 0, tzinfo=UTC)
    assert query.to_at == datetime(2026, 8, 14, 7, 0, tzinfo=UTC)
    assert query.limit == 20
    assert query.notes == ()


def test_코드펜스로_감싼_응답도_읽는다() -> None:
    text = f"```json\n{json.dumps(_VALID)}\n```"

    query = parse_plan(text, now=_NOW, max_span_days=7, limit_ceiling=20)

    assert query.classroom_id == "A101"


def test_앞뒤에_설명이_붙어도_읽는다() -> None:
    text = f"네, 아래와 같이 변환했습니다.\n{json.dumps(_VALID)}\n필요하면 알려주세요."

    query = parse_plan(text, now=_NOW, max_span_days=7, limit_ceiling=20)

    assert query.classroom_id == "A101"


def test_시각대가_다른_표기도_UTC로_맞춘다() -> None:
    payload = {**_VALID, "from": "2026-08-14T15:00:00+09:00", "to": "2026-08-14T16:00:00+09:00"}

    query = parse_plan(json.dumps(payload), now=_NOW, max_span_days=7, limit_ceiling=20)

    assert query.from_at == datetime(2026, 8, 14, 6, 0, tzinfo=UTC)


def test_빈_문자열_식별자는_지정하지_않은_것으로_본다() -> None:
    payload = {**_VALID, "classroom_id": "   "}

    query = parse_plan(json.dumps(payload), now=_NOW, max_span_days=7, limit_ceiling=20)

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

    query = parse_plan(json.dumps(payload), now=_NOW, max_span_days=7, limit_ceiling=20)

    assert query.to_at == datetime(2026, 8, 14, 0, 0, tzinfo=UTC)
    assert query.from_at == datetime(2026, 8, 7, 0, 0, tzinfo=UTC)
    assert query.notes == ("조회 기간이 너무 길어 마지막 7일만 찾았습니다.",)


def test_모델이_요청_상한을_넘기면_상한으로_깎고_알린다() -> None:
    """모델이 호출자의 요청을 덮어쓸 수 있으면 API 계약이 깨진다."""
    payload = {**_VALID, "limit": 100000}

    query = parse_plan(json.dumps(payload), now=_NOW, max_span_days=7, limit_ceiling=8)

    assert query.limit == 8
    assert len(query.notes) == 1


def test_요청_상한도_절대_상한을_넘지_못한다() -> None:
    payload = {**_VALID, "limit": 100000}

    query = parse_plan(json.dumps(payload), now=_NOW, max_span_days=7, limit_ceiling=100000)

    assert query.limit == MAX_LIMIT


def test_limit이_없으면_요청_상한을_쓴다() -> None:
    payload = {key: value for key, value in _VALID.items() if key != "limit"}

    query = parse_plan(json.dumps(payload), now=_NOW, max_span_days=7, limit_ceiling=5)

    assert query.limit == 5
    assert query.notes == ()


def test_생성_스키마와_검증_규격이_어긋나지_않는다() -> None:
    """스키마는 생성 힌트고 `parse_plan`은 검증이다. 둘이 갈라지면 모델이 스키마를
    지켜서 낸 응답이 422가 된다 — 고치기 가장 어려운 종류의 실패다.

    스키마가 허용하는 키만으로 만든 응답이 실제로 통과하는지로 확인한다. 키 집합을
    직접 비교하지 않는 이유는, 통과 여부가 진짜 계약이기 때문이다.
    """
    properties = PLAN_JSON_SCHEMA["properties"]
    assert isinstance(properties, dict)

    payload: dict[str, object] = {
        "intent": "detection_search",
        "camera_id": None,
        "classroom_id": "A101",
        "from": "2026-08-14T06:00:00+09:00",
        "to": "2026-08-14T07:00:00+09:00",
        "limit": 10,
    }
    assert set(payload) == set(properties)

    query = parse_plan(json.dumps(payload), now=_NOW, max_span_days=7, limit_ceiling=MAX_LIMIT)

    assert query.limit == 10
    assert query.notes == ()


def test_스키마가_모르는_키를_막는다() -> None:
    """생성 단계에서 막아도 검증은 그대로 한다. 서버가 스키마를 무시할 수 있다."""
    assert PLAN_JSON_SCHEMA["additionalProperties"] is False
    assert _reason(json.dumps({**_VALID, "order_by": "captured_at"})) == "UNKNOWN_FIELD"


def test_스키마가_여섯_키를_모두_내게_한다() -> None:
    """optional로 둔 키는 모델이 통째로 생략한다. **검증이 잡지 못하는 실패다.**

    2026-08-18 GPU 서버(gemma-2-9b-it Q4_K_M, llama.cpp b10362)에서 실측했다.
    required가 intent/from/to뿐일 때 camera_id와 classroom_id가 한 번도 나오지
    않았고, "A동 201호 상황 보여줘"가 전체 카메라를 뒤지는 계획이 되었다.
    llama.cpp가 grammar로 바꾸며 optional을 생략 가능으로 만들고, temperature가
    0이라 생성이 언제나 같은 쪽을 고른다.

    parse_plan은 이것을 막을 수 없다 — 키가 없는 것과 "특정하지 않음"이 둘 다
    None이 되기 때문이다. 그래서 규격 쪽에서 고정한다.

    **키를 강제하는 것이지 값을 강제하는 것이 아니다.** 대상이 없으면 모델은 null을
    쓰고, 아래 테스트가 그 응답이 통과하는 것을 함께 고정한다.
    """
    properties = PLAN_JSON_SCHEMA["properties"]
    required = PLAN_JSON_SCHEMA["required"]
    assert isinstance(properties, dict)
    assert isinstance(required, list)

    assert set(required) == set(properties)


def test_대상을_특정하지_않은_응답도_통과한다() -> None:
    """required가 늘어도 null은 그대로 받는다. 둘이 어긋나면 모델이 규격을 지키고도
    422가 된다 — 전체 조회를 뜻하는 null이 거부되면 기능의 절반이 막힌다."""
    payload = {**_VALID, "camera_id": None, "classroom_id": None}

    query = parse_plan(json.dumps(payload), now=_NOW, max_span_days=7, limit_ceiling=20)

    assert query.camera_id is None
    assert query.classroom_id is None


def test_미래로_넘어간_끝을_지금으로_자른다() -> None:
    """모델이 "지난 한 달"을 앞으로 한 달로 잡는 일이 있었다(2026-08-18 실측).

    자르는 이유는 결과를 바꾸기 위해서가 아니라 **기간 상한 절삭의 기준점을 현실로
    되돌리기 위해서다.** to가 미래인 채로 7일을 세면 구간이 통째로 미래에 남아
    언제나 0건이 되고, 화면에는 "그 시간에 아무도 없었다"로 보인다.
    """
    payload = {**_VALID, "from": "2026-07-19T00:00:00Z", "to": "2026-09-19T00:00:00Z"}

    query = parse_plan(json.dumps(payload), now=_NOW, max_span_days=7, limit_ceiling=20)

    assert query.to_at == _NOW
    assert query.from_at == _NOW - timedelta(days=7)


def test_미래를_자를_때는_사유를_남기지_않는다() -> None:
    """아직 오지 않은 시각에는 데이터가 없어 결과가 달라지지 않는다.

    "오늘 하루"처럼 끝을 자정으로 잡는 정상적인 계획마다 안내가 붙으면, 정작 읽어야
    할 조정 사유(기간·건수)가 묻힌다.
    """
    payload = {**_VALID, "from": "2026-08-14T06:00:00Z", "to": "2026-08-15T00:00:00Z"}

    query = parse_plan(json.dumps(payload), now=_NOW, max_span_days=7, limit_ceiling=20)

    assert query.to_at == _NOW
    assert query.notes == ()


def test_구간_전체가_미래면_거부한다() -> None:
    """잘라 낼 수 없다. 조용히 0건을 돌려주면 "그때 아무도 없었다"로 읽힌다."""
    payload = {**_VALID, "from": "2026-08-20T00:00:00Z", "to": "2026-08-21T00:00:00Z"}

    assert _reason(json.dumps(payload)) == "FUTURE_RANGE"


def test_지금까지의_구간은_그대로_둔다() -> None:
    """경계에서 멀쩡한 계획이 깎이면 안 된다."""
    payload = {**_VALID, "from": "2026-08-14T06:00:00Z", "to": "2026-08-14T12:00:00Z"}

    query = parse_plan(json.dumps(payload), now=_NOW, max_span_days=7, limit_ceiling=20)

    assert query.to_at == _NOW
    assert query.notes == ()
