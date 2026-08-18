"""LLM이 낸 원문을 검증된 검색 조건으로 바꾼다.

**이 파일이 "LLM은 JSON을 만들고, 검증은 fastapi가 한다"의 구현체다.**
순수 함수만 둔다. HTTP도 저장소도 모르고, LLM 없이 테스트할 수 있다.

## 왜 이렇게까지 방어하는가

모델 출력은 **신뢰할 수 없는 입력**이다. 프롬프트 인젝션을 걱정해서가 아니라,
작은 모델이 평범하게 실패하는 방식이 많기 때문이다 — 설명을 덧붙이고, 코드펜스로
감싸고, 없는 키를 지어내고, 시각대를 빼먹는다. 여기서 막는 것:

- 본문 크기 상한. 어댑터가 `max_tokens`를 걸지만 그것 하나만 믿지 않는다
- 코드펜스와 앞뒤 설명 제거
- 최상위가 객체가 아닌 JSON 거부. `[...]`, `"문자열"`, `123`도 유효한 JSON이다
- 알 수 없는 키 거부. 저장소 질의로 흘러가는 값을 **우리가 아는 것만** 남긴다
- `intent` 허용 목록

여기를 통과한 값만 `SearchQuery`가 되고, 저장소는 `SearchQuery`만 본다.
모델 출력이 질의 조각으로 그대로 들어가는 경로는 없다.

## 오류로 만들지 않는 것

기간이 너무 길거나 `limit`이 크면 **거절하지 않고 줄인 뒤 그 사실을 알린다.**
"이번 달"은 일상적인 질문인데 매번 422를 돌려주면 쓸 수 없다. 다만 조용히 줄이면
사용자가 잘못된 답을 맞는 답으로 읽으므로 `notes`에 반드시 남긴다.

## 미래는 잘라 낸다

모델이 "지난 한 달"을 앞으로 한 달로 잡는 일이 실제로 있었다(2026-08-18 실측).
미래 구간은 탐지가 존재할 수 없어 언제나 0건인데, 화면에는 "그 시간에 아무도
없었다"로 보인다.

**이 절삭만은 `notes`를 남기지 않는다.** 결과가 달라지지 않기 때문이다 — 아직 오지
않은 시각에는 데이터가 없으므로 자르든 말든 같은 결과다. "오늘 하루"처럼 끝을
자정으로 잡는 정상적인 계획마다 안내가 붙으면 정작 읽어야 할 조정 사유가 묻힌다.
자르는 진짜 이유는 따로 있다. **기간 상한 절삭의 기준점을 현실로 되돌리는 것**이다.
`to`가 미래인 채로 7일을 세면 결과 구간이 통째로 미래에 남는다.

구간 전체가 미래면 자를 수 없으므로 그때는 오류다. 사용자가 할 수 있는 일이
질문을 고치는 것뿐이라 `EMPTY_RANGE`와 같은 성격이다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from .errors import LlmSearchPlanInvalidError
from .models import SearchQuery

# 정상 응답은 200바이트 남짓이다. 이 상한은 "조금 장황한 모델"이 아니라
# "멈추지 않는 모델"을 걸러내기 위한 것이라 넉넉하게 잡는다.
MAX_PLAN_TEXT_BYTES: Final = 8192

# app/video_monitoring/schemas.py의 VideoSearchRequest와 같은 상한을 쓴다.
# 검색 결과 수의 의미가 화면마다 다르면 사용자가 혼란스럽다.
MAX_LIMIT: Final = 50

_INTENT: Final = "detection_search"
_ALLOWED_KEYS: Final = frozenset({"intent", "camera_id", "classroom_id", "from", "to", "limit"})
_MAX_IDENTIFIER_LENGTH: Final = 128

# 어댑터가 생성 단계에서 구조를 강제하는 데 쓴다(llama.cpp가 grammar로 바꿔 준다).
# **검증이 아니라 생성 힌트다.** 이 스키마를 통과한 값도 아래 `parse_plan`을 전부
# 거친다 — 서버가 스키마를 무시하거나 지원하지 않을 수 있고, 스키마로는 표현되지
# 않는 규칙(시각대, from < to, 기간 상한)이 남기 때문이다.
#
# 여기 두는 이유는 키 목록의 정본이 이 파일이기 때문이다. 어댑터에 두면 규격이
# 두 곳에서 갈라진다. 정합성은 test_planning.py가 고정한다.
#
# `const`가 아니라 `enum`을 쓰고 시각에 `pattern`을 걸지 않은 것은 의도적이다.
# JSON Schema의 표현 중 grammar로 변환되지 않는 것이 있으면 서버가 요청을 통째로
# 거절할 수 있어, 확실히 지원되는 표현만 쓴다.
#
# **`required`에 여섯 키를 전부 넣는다. 하나라도 빼면 그 키는 영영 생성되지 않는다.**
# llama.cpp가 스키마를 grammar로 바꿀 때 optional 필드를 "생략 가능"으로 만들고,
# `temperature: 0`이라 모델은 언제나 같은 선택 — 생략 — 을 한다. 2026-08-18 GPU
# 서버(gemma-2-9b-it)에서 `intent`/`from`/`to`만 required였을 때 `camera_id`와
# `classroom_id`가 한 번도 나오지 않았고, 그래서 "A동 201호"를 물어도 전체 카메라를
# 뒤지는 계획이 만들어졌다. 아래 `parse_plan`은 이것을 잡지 못한다 — 키가 없는 것과
# "특정하지 않음"이 같은 값(None)이 되기 때문이다.
#
# 값을 강제하는 것이 아니라 **키를 강제하는 것**이다. 타입이 `["string", "null"]`이라
# 대상이 없으면 모델은 그대로 null을 쓴다.
PLAN_JSON_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "properties": {
        "intent": {"enum": [_INTENT]},
        "camera_id": {"type": ["string", "null"]},
        "classroom_id": {"type": ["string", "null"]},
        "from": {"type": "string"},
        "to": {"type": "string"},
        "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
    },
    "required": ["intent", "camera_id", "classroom_id", "from", "to", "limit"],
    "additionalProperties": False,
}


def parse_plan(
    text: str,
    *,
    now: datetime,
    max_span_days: int,
    limit_ceiling: int,
) -> SearchQuery:
    """모델 원문을 검증된 `SearchQuery`로 바꾼다.

    규격을 벗어나면 `LlmSearchPlanInvalidError`를 던진다. 사유 코드는 우리가 정의한
    값이며 모델이 쓴 글자를 절대 담지 않는다.

    `limit_ceiling`은 **호출자가 요청한 상한**이다. 모델이 더 큰 수를 내도 이 값을
    넘지 못한다. 모델이 호출자의 요청을 덮어쓸 수 있으면 API 계약이 깨진다 —
    3건을 요청했는데 20건이 오는 일이 생긴다.

    `now`는 프롬프트를 만들 때 쓴 것과 **같은 값이어야 한다.** 지시문의 "지금"과
    검증의 "지금"이 다르면 모델이 규격을 지켜 낸 계획이 경계에서 거부된다.
    """
    if len(text.encode("utf-8")) > MAX_PLAN_TEXT_BYTES:
        raise LlmSearchPlanInvalidError("PLAN_TOO_LARGE")

    payload = _load_object(text)

    unknown = set(payload) - _ALLOWED_KEYS
    if unknown:
        # 어떤 키였는지 응답에 싣지 않는다. 모델이 쓴 글자다.
        raise LlmSearchPlanInvalidError("UNKNOWN_FIELD")

    if payload.get("intent") != _INTENT:
        raise LlmSearchPlanInvalidError("UNSUPPORTED_INTENT")

    from_at = _required_datetime(payload, "from")
    to_at = _required_datetime(payload, "to")
    if from_at >= to_at:
        # 저장소가 반개구간(from <= x < to)으로 조회한다. 두 값이 같으면 결과가
        # 항상 0건인데, 사용자에게는 "그 시간에 아무도 없었다"로 보인다.
        raise LlmSearchPlanInvalidError("EMPTY_RANGE")

    if to_at > now:
        if from_at >= now:
            # 구간 전체가 미래다. 자르면 빈 구간이 되므로 알려 주는 편이 낫다.
            raise LlmSearchPlanInvalidError("FUTURE_RANGE")
        to_at = now

    notes: list[str] = []

    max_span = timedelta(days=max_span_days)
    if to_at - from_at > max_span:
        from_at = to_at - max_span
        notes.append(f"조회 기간이 너무 길어 마지막 {max_span_days}일만 찾았습니다.")

    limit, limit_note = _limit(payload, limit_ceiling)
    if limit_note is not None:
        notes.append(limit_note)

    return SearchQuery(
        camera_id=_optional_identifier(payload, "camera_id"),
        classroom_id=_optional_identifier(payload, "classroom_id"),
        from_at=from_at,
        to_at=to_at,
        limit=limit,
        notes=tuple(notes),
    )


def _load_object(text: str) -> dict[str, Any]:
    """원문에서 JSON 객체를 꺼낸다.

    먼저 전체를 그대로 읽어 본다. 실패했을 때만 첫 `{`부터 마지막 `}`까지를 잘라
    다시 읽는다. 순서가 중요하다 — 처음부터 중괄호를 잘라내면 `[{...}]`처럼 최상위가
    배열인 응답에서 안쪽 객체가 튀어나와, 규격 위반이 조용히 통과한다.
    """
    stripped = text.strip()
    document = _loads_or_none(stripped)
    if document is None:
        document = _loads_or_none(_slice_braces(stripped))
    if document is None:
        raise LlmSearchPlanInvalidError("NOT_JSON")
    if not isinstance(document, dict):
        raise LlmSearchPlanInvalidError("NOT_OBJECT")
    return document


def _loads_or_none(text: str) -> object:
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _slice_braces(text: str) -> str:
    """코드펜스나 앞뒤 설명에 감싸인 객체를 꺼낸다."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end < start:
        return ""
    return text[start : end + 1]


def _optional_identifier(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise LlmSearchPlanInvalidError("INVALID_TYPE")
    trimmed = value.strip()
    if not trimmed:
        # 모델이 "특정하지 않음"을 빈 문자열로 표현하는 일이 잦다. null과 같게 본다.
        return None
    if len(trimmed) > _MAX_IDENTIFIER_LENGTH:
        raise LlmSearchPlanInvalidError("IDENTIFIER_TOO_LONG")
    return trimmed


def _required_datetime(payload: dict[str, Any], key: str) -> datetime:
    value = payload.get(key)
    if value is None:
        raise LlmSearchPlanInvalidError("MISSING_FIELD")
    if not isinstance(value, str):
        raise LlmSearchPlanInvalidError("INVALID_TYPE")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise LlmSearchPlanInvalidError("INVALID_DATETIME") from None
    if parsed.tzinfo is None:
        # 시각대 없는 값을 UTC로 가정하면 9시간 어긋난 결과를 정상처럼 돌려준다.
        raise LlmSearchPlanInvalidError("NAIVE_DATETIME")
    return parsed.astimezone(UTC)


def _limit(payload: dict[str, Any], ceiling: int) -> tuple[int, str | None]:
    effective_ceiling = _clamp(ceiling, MAX_LIMIT)
    value = payload.get("limit")
    if value is None:
        return effective_ceiling, None
    # bool은 int의 하위 타입이라 isinstance만으로는 걸러지지 않는다. true가 1이 된다.
    if isinstance(value, bool) or not isinstance(value, int):
        raise LlmSearchPlanInvalidError("INVALID_TYPE")
    clamped = _clamp(value, effective_ceiling)
    if clamped != value:
        return clamped, f"결과를 최대 {clamped}건까지만 보여 줍니다."
    return clamped, None


def _clamp(value: int, ceiling: int) -> int:
    return min(max(value, 1), ceiling)
