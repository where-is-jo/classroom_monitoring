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

## 사람 조건은 여기서 판정하지 않는다

`person_name`은 모델이 질문에서 옮겨 적은 이름 그대로 통과시킨다. 학생 원장에 있는
이름인지 대조하는 일은 서비스가 한다 — 이 파일은 저장소를 모르고, 그래야 LLM 없이
검증만 시험할 수 있다. `person_presence`("있는"인가 "없는"인가)는 값이 빠지거나
목록 밖이어도 거부하지 않는다. 이유는 `_person_presence`에 적었다.

## 미래는 잘라 낸다

모델이 "지난 한 달"을 앞으로 한 달로 잡는 일이 실제로 있었다(2026-08-18 실측).
미래 구간은 탐지가 존재할 수 없어 언제나 0건인데, 화면에는 "그 시간에 아무도
없었다"로 보인다.

**이 절삭만은 `notes`를 남기지 않는다.** 결과가 달라지지 않기 때문이다 — 아직 오지
않은 시각에는 데이터가 없으므로 자르든 말든 같은 결과다. "오늘 하루"처럼 끝을
자정으로 잡는 정상적인 계획마다 안내가 붙으면 정작 읽어야 할 조정 사유가 묻힌다.
자르는 진짜 이유는 따로 있다. **기간 상한 절삭의 기준점을 현실로 되돌리는 것**이다.
`to`가 미래인 채로 7일을 세면 결과 구간이 통째로 미래에 남는다.

구간 전체가 미래면 자를 수 없다. 그때는 **12시간을 되돌려 본다** — 오전·오후 기본값을
뒤집은 것이 원인인 경우가 대부분이라(`_pull_back_half_day`) 되돌리면 사용자가 물은
구간이 나온다. 되돌려도 미래면 그때는 오류다. 사용자가 할 수 있는 일이 질문을 고치는
것뿐이라 `EMPTY_RANGE`와 같은 성격이다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from .errors import LlmSearchPlanInvalidError
from .models import PersonPresence, SearchQuery

# 정상 응답은 200바이트 남짓이다. 이 상한은 "조금 장황한 모델"이 아니라
# "멈추지 않는 모델"을 걸러내기 위한 것이라 넉넉하게 잡는다.
MAX_PLAN_TEXT_BYTES: Final = 8192

# 화면이 20건씩 다섯 쪽으로 나눠 보여주므로 다섯 쪽을 채우는 수다.
# 영상 검색(app/video_monitoring/schemas.py)의 50과 갈리는데, 그쪽은 한 화면에
# 끝까지 늘어놓는 목록이라 50이 넘으면 스크롤로만 읽어야 한다. 여기는 쪽이 나뉘어
# 100건이어도 한 화면에 20건씩만 놓인다. **같은 수를 쓰는 것보다 각자 화면이
# 감당하는 수를 쓰는 편이 맞다.**
MAX_LIMIT: Final = 100

_INTENT: Final = "detection_search"
_ALLOWED_KEYS: Final = frozenset(
    {
        "intent",
        "camera_id",
        "classroom_id",
        "from",
        "to",
        "limit",
        "person_name",
        "person_presence",
    }
)
_MAX_IDENTIFIER_LENGTH: Final = 128

# 사람 이름 상한을 식별자와 따로 두는 이유는 값의 성격이 다르기 때문이다. 식별자는
# UUID라 128자가 넉넉하지만, 사람 이름이 그만큼 길게 오면 그것은 이름이 아니라
# 모델이 질문 문장을 통째로 옮긴 것이다. 그 값이 학생 원장 조회로 흘러가도 결과는
# 언제나 "찾지 못함"이라 해롭지는 않으나, 화면에 그대로 인용되므로 짧게 자른다.
_MAX_PERSON_NAME_LENGTH: Final = 32

# 오전·오후를 뒤집는 폭. `_pull_back_half_day`가 유일한 사용처다.
_HALF_DAY: Final = timedelta(hours=12)

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
        "person_name": {"type": ["string", "null"]},
        "person_presence": {"enum": [member.value for member in PersonPresence]},
    },
    "required": [
        "intent",
        "camera_id",
        "classroom_id",
        "from",
        "to",
        "limit",
        "person_name",
        "person_presence",
    ],
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

    notes: list[str] = []

    if to_at > now:
        if from_at >= now:
            from_at, to_at = _pull_back_half_day(from_at, to_at, now)
            notes.append(
                "오후로 읽으면 아직 오지 않은 시각이라 오전으로 바꿔 찾았습니다. "
                "오후를 뜻하셨다면 그 시간이 지난 뒤에 다시 물어봐 주세요."
            )
        else:
            to_at = now

    max_span = timedelta(days=max_span_days)
    if to_at - from_at > max_span:
        from_at = to_at - max_span
        notes.append(f"조회 기간이 너무 길어 마지막 {max_span_days}일만 찾았습니다.")

    limit, limit_note = _limit(payload, limit_ceiling)
    if limit_note is not None:
        notes.append(limit_note)

    person_name = _optional_identifier(payload, "person_name", max_length=_MAX_PERSON_NAME_LENGTH)
    return SearchQuery(
        camera_id=_optional_identifier(payload, "camera_id"),
        classroom_id=_optional_identifier(payload, "classroom_id"),
        from_at=from_at,
        to_at=to_at,
        limit=limit,
        person_name=person_name,
        person_presence=_person_presence(payload, person_name),
        notes=tuple(notes),
    )


def _pull_back_half_day(
    from_at: datetime, to_at: datetime, now: datetime
) -> tuple[datetime, datetime]:
    """구간 전체가 미래일 때 12시간을 되돌려 본다. 그래도 미래면 오류다.

    **오전·오후 기본값의 뒷정리다.** 지시문은 오전인지 밝히지 않은 1시~11시를 오후로
    읽게 한다(`prompts.py`). 강의실을 쓰는 시간대가 낮이라 대개 맞지만, **아직 그
    시각이 오지 않았으면 반드시 틀린다** — 오전 3시를 뜻한 질문이 오후 3시가 된다.

    2026-08-25 GPU 서버(gemma) 실측: KST 14:00에 "오늘 3시부터 4시 사이에 강의실에
    몇 명 있었어?"가 15:00~16:00으로 나왔고, 재시도까지 같은 값이라 사용자는 422를
    받았다. 지시문에 "오후로 읽으면 지금보다 뒤가 되는 시각은 오전으로 읽어라"를
    넣고 다시 측정했으나 결과는 같았다 — 작은 모델이 "지금"과 생성 중인 시각을
    비교하지 못한다. 그래서 **모델에게 맡기지 않고 여기서 되돌린다.**

    12시간이라는 폭이 곧 판정 근거다. 되돌려서 과거가 되는 구간은 오전·오후를 뒤집은
    것이고, 되돌려도 미래인 구간(예: 모델이 내일 날짜를 낸 경우)은 성격이 다르므로
    그대로 오류로 둔다. 자르지 않는 이유는 잘라 봐야 빈 구간이 되기 때문이다.

    **조용히 바꾸지 않는다.** 호출자가 `notes`에 사유를 남긴다. 오후를 정말로 뜻한
    사용자는 그 문장을 보고 자기가 물은 시각이 아직 오지 않았음을 안다.
    """
    shifted_from = from_at - _HALF_DAY
    shifted_to = to_at - _HALF_DAY
    if shifted_to > now:
        raise LlmSearchPlanInvalidError("FUTURE_RANGE")
    return shifted_from, shifted_to


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


def _optional_identifier(
    payload: dict[str, Any], key: str, *, max_length: int = _MAX_IDENTIFIER_LENGTH
) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise LlmSearchPlanInvalidError("INVALID_TYPE")
    trimmed = value.strip()
    if not trimmed:
        # 모델이 "특정하지 않음"을 빈 문자열로 표현하는 일이 잦다. null과 같게 본다.
        return None
    if len(trimmed) > max_length:
        raise LlmSearchPlanInvalidError("IDENTIFIER_TOO_LONG")
    return trimmed


def _person_presence(payload: dict[str, Any], person_name: str | None) -> PersonPresence:
    """ "있는"인가 "없는"인가를 읽는다. **알 수 없으면 오류로 만들지 않는다.**

    이름을 옮기는 데는 성공했는데 방향을 빠뜨린 응답이 422가 되면, 사용자는 답을
    받을 수 있었던 질문에 "다시 써 주세요"를 듣는다. 그래서 값이 없거나 허용 목록
    밖이면 `PRESENT`로 읽는다 — **사람을 지목한 질문의 압도적 다수가 "있는"이다.**

    이름이 없는데 방향만 온 경우는 `ANY`다. 걸러 낼 대상이 없으므로 방향에 의미가
    없고, 그대로 두면 서비스가 이름 `None`으로 필터를 만들려다 갈라진다.

    허용 목록 밖의 값을 거부하지 않는 것은 이 필드가 **저장소로 흘러가지 않기**
    때문이다. 서비스는 이 enum으로 분기만 하고 값을 조회 조건에 싣지 않는다.
    """
    if person_name is None:
        return PersonPresence.ANY
    value = payload.get("person_presence")
    if not isinstance(value, str):
        return PersonPresence.PRESENT
    try:
        presence = PersonPresence(value.strip().lower())
    except ValueError:
        return PersonPresence.PRESENT
    # 이름을 지목했는데 any가 오면 모델이 방향을 판단하지 못한 것이다. 걸러 내지
    # 않으면 이름을 화면에만 적고 결과는 전체를 주게 되므로 기본값으로 되돌린다.
    return PersonPresence.PRESENT if presence is PersonPresence.ANY else presence


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
