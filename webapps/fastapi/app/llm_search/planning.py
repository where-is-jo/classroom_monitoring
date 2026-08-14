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


def parse_plan(
    text: str,
    *,
    max_span_days: int,
    default_limit: int,
) -> SearchQuery:
    """모델 원문을 검증된 `SearchQuery`로 바꾼다.

    규격을 벗어나면 `LlmSearchPlanInvalidError`를 던진다. 사유 코드는 우리가 정의한
    값이며 모델이 쓴 글자를 절대 담지 않는다.
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

    notes: list[str] = []

    max_span = timedelta(days=max_span_days)
    if to_at - from_at > max_span:
        from_at = to_at - max_span
        notes.append(f"조회 기간이 너무 길어 마지막 {max_span_days}일만 찾았습니다.")

    limit, limit_note = _limit(payload, default_limit)
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


def _limit(payload: dict[str, Any], default_limit: int) -> tuple[int, str | None]:
    value = payload.get("limit")
    if value is None:
        return _clamp(default_limit), None
    # bool은 int의 하위 타입이라 isinstance만으로는 걸러지지 않는다. true가 1이 된다.
    if isinstance(value, bool) or not isinstance(value, int):
        raise LlmSearchPlanInvalidError("INVALID_TYPE")
    clamped = _clamp(value)
    if clamped != value:
        return clamped, f"한 번에 보여줄 수 있는 최대 {MAX_LIMIT}건으로 줄였습니다."
    return clamped, None


def _clamp(value: int) -> int:
    return min(max(value, 1), MAX_LIMIT)
