"""자연어 검색의 도메인 모델.

`SearchQuery`는 **검증을 통과한 뒤에만** 만들어진다. 이 타입의 값이 손에 있다는 것은
"LLM이 뭐라고 했든 저장소에 넣어도 되는 조건"이라는 뜻이다. 검증 전 상태를 담는
타입을 따로 두지 않는 이유도 그것이다 — 검증 전 값은 `dict`로만 존재하고
`planning.py` 밖으로 나가지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


@dataclass(frozen=True)
class CameraChoice:
    """LLM에게 알려줄 카메라 하나.

    프롬프트에 실제 등록된 식별자를 넣어야 모델이 없는 강의실을 지어내지 않는다.
    `video_monitoring`의 `VideoStream`을 그대로 쓰지 않는 이유는 프롬프트 조립이
    순수 함수로 남아야 하기 때문이다. 필요한 값만 옮겨 담는다.

    **`classroom_code`와 `classroom_name`이 없으면 모델은 사람이 부르는 이름을
    식별자로 옮길 수 없다.** `classroom_id`는 UUID라 질문에 등장하지 않는다.
    2026-08-23 실측: 목록에 UUID만 있는 상태에서 "A111 강의실에 오늘 몇 명
    있었어?"를 물으면 모델이 `classroom_id="A111"`을 그대로 내고, 서버는 등록되지
    않은 강의실로 판정해 0건을 돌려줬다.

    강의실이 `classrooms`에 없으면 둘 다 `None`이다. 스트림에는 `classroom_id`만
    담겨 있어 등록이 지워져도 스트림은 남는다.
    """

    camera_id: str
    classroom_id: str
    label: str
    classroom_code: str | None
    classroom_name: str | None


class PersonPresence(StrEnum):
    """질문이 지목한 사람이 **있어야 하는지 없어야 하는지.**

    "박무현이 있는"과 "박무현이 없는"은 같은 기간·같은 강의실을 보면서 **정반대의
    결과**를 요구한다. 사람 이름만 뽑고 이 방향을 버리면 두 질문이 서버에서 같아져,
    "없는 사진"을 물은 사람이 "있는 사진"을 받는다.

    `ANY`는 사람을 말하지 않았을 때다. `None`으로 표현하지 않는 이유는 `person_name`이
    이미 `None`으로 "아무도 지목하지 않음"을 나타내고 있어, 두 필드가 각각 없음을
    가지면 조합이 네 가지가 되기 때문이다.
    """

    ANY = "any"
    PRESENT = "present"
    ABSENT = "absent"


@dataclass(frozen=True)
class SearchQuery:
    """검증된 검색 조건.

    시각은 항상 시각대를 가진 UTC이고 `from_at < to_at`이다. 저장소가 반개구간
    (`from_at <= x < to_at`)으로 조회하므로 두 값이 같으면 조용히 0건이 된다.

    `notes`는 사용자에게 그대로 보여줄 한국어 문장이다. 요청을 조정했으면
    (기간 절삭, limit 제한) 반드시 여기에 남긴다. **조용히 줄이지 않는다.**

    `person_name`은 **모델이 질문에서 옮겨 적은 이름 그대로**다. 학생 원장과 대조하는
    일은 서비스가 한다 — 여기(`planning.py`)는 저장소를 모른다.
    """

    camera_id: str | None
    classroom_id: str | None
    from_at: datetime
    to_at: datetime
    limit: int
    person_name: str | None
    person_presence: PersonPresence
    notes: tuple[str, ...]


@dataclass(frozen=True)
class IdentifiedStudent:
    """탐지된 사람 중 신원이 붙은 한 명.

    지금은 이 값을 채우는 생산자가 없다 — `deeplearning`은 얼굴 검출까지고 인식은
    미구현이다. 그래도 자리를 만들어 두는 이유는 **탐지 이벤트 스키마에 이미 필드가
    있고**, 인식이 붙는 순간 코드 변경 없이 화면에 이름이 뜨게 하기 위해서다.
    """

    student_id: str
    identity_confidence: float | None


@dataclass(frozen=True)
class DetectionHit:
    """검색 결과 한 줄.

    식별된 사람과 그렇지 않은 사람을 **수로 구분해** 담는다. `identified`가 비어
    있다는 것과 "아무도 없었다"는 전혀 다른 이야기이고, 화면이 둘을 섞으면 없는
    사실을 만들어 낸다.

    `resolved_classroom_id`는 **지금 기준으로 해석한** 값이다. 탐지 이벤트 자체에는
    강의실이 담기지 않아 카메라 등록 정보로 되짚는다. 카메라를 다른 강의실로 옮기면
    과거 이벤트도 새 강의실로 보인다.

    `resolved_classroom_label`은 같은 강의실을 **사람이 읽는 이름**으로 적은 것이다.
    식별자가 UUID라 화면에 그대로 내보내면 아무 정보가 되지 못한다. 둘을 함께 두는
    이유는 API 응답이 식별자를 계속 돌려줘야 하기 때문이다 — 호출자가 다른 API에
    이어 쓰는 값이다.

    `snapshot_key`는 저장소에 실제로 있는 키이거나 `None`이다. 계산만 하고 존재를
    확인하지 않은 키는 여기 담지 않는다.
    """

    event_id: str
    camera_id: str
    resolved_classroom_id: str
    resolved_classroom_label: str
    captured_at: datetime
    detection_count: int
    identified: tuple[IdentifiedStudent, ...]
    unidentified_count: int
    snapshot_key: str | None


@dataclass(frozen=True)
class PersonSummary:
    """질문이 지목한 사람과, 그 조건을 실제로 적용했는지.

    **`applied`가 거짓이면 아래 결과는 사람 조건이 걸리지 않은 목록이다.** 이 값을
    빼고 이름만 화면에 두면, 걸러지지 않은 목록이 "박무현이 없는 기록"이라는 제목을
    달고 나간다. 없는 판정을 만들어 내는 셈이라 반드시 함께 돌려준다.

    적용하지 못하는 경우가 셋이다 — 학생 원장에 그 이름이 없을 때, 같은 이름이
    여럿이라 누구인지 고를 수 없을 때, 그리고 조회 구간의 탐지에 신원이 하나도
    실려 있지 않을 때(`identity_available is False`). 마지막이 지금의 기본 상태다.
    얼굴 인식이 아직 연결되지 않아 `Detection.student_id`를 채우는 생산자가 없다.

    앞의 두 경우를 `match_count`로 나누는 이유는 **사용자에게 할 말이 다르기**
    때문이다. 0이면 오타를 고치면 되고, 2 이상이면 이름만으로는 영영 고를 수 없어
    학번 같은 다른 단서가 필요하다. `student_id`만 보면 둘이 같아져, 동명이인을
    물은 사람이 "명부에 없습니다"라는 틀린 안내를 받는다.
    """

    name: str
    presence: PersonPresence
    student_id: str | None
    match_count: int
    identity_available: bool
    applied: bool


class SortOrder(StrEnum):
    """결과를 시간 어느 쪽부터 보여줄지.

    **`SearchQuery`가 아니라 여기에 두는 이유가 있다.** `SearchQuery`는 LLM이 만든
    계획을 검증한 결과이고, 정렬은 모델이 관여하지 않는 값이다 — 사용자가 화면에서
    고른다. 섞어 두면 "모델이 뭐라고 했든 저장소에 넣어도 되는 조건"이라는
    `SearchQuery`의 뜻이 흐려지고, 모델이 정렬을 지어내는지 검증해야 할 것처럼 읽힌다.

    상한(`limit`)이 있으므로 이 값은 **보이는 순서만 바꾸는 것이 아니다.** 내림차순은
    가장 최근 100건을, 오름차순은 가장 오래된 100건을 고른다. 정렬을 화면에서
    뒤집기만 하지 않고 조회에 함께 넣는 이유가 그것이다.
    """

    TIME_DESC = "time_desc"
    TIME_ASC = "time_asc"


@dataclass(frozen=True)
class SearchOutcome:
    """검색 한 번의 결과 전부.

    `truncated`와 `snapshot_lookup_failed`를 결과와 함께 돌려주는 이유는 화면이
    "없음"과 "못 봤음"을 구분해야 하기 때문이다. 둘을 같게 보여주면 운영자가
    데이터가 없다고 판단한다.

    `target_label`은 이번 검색이 무엇을 대상으로 삼았는지 한 문장으로 적은 값이다.
    "카메라를 콕 집었는가 / 강의실인가 / 전체인가"의 판정과 UUID를 이름으로 바꾸는
    일을 **서비스에서 끝내려고** 둔다. 템플릿이 식별자를 보고 분기하면 같은 판정이
    화면마다 복사되고, 강의실 이름을 붙이려면 템플릿이 저장소를 알아야 한다.

    `briefing`은 같은 내용을 사람이 읽는 한두 문장으로 적은 것이다. 기간·대상·건수가
    `query`와 `target_label`과 `hits`에 흩어져 있어, 화면이 그것을 문장으로 잇는
    순간 **표기 규칙(오늘인가 어제인가, 초를 보일 것인가)이 템플릿으로 샌다.**
    조립을 서비스에서 끝낸다 — 규칙 해석을 템플릿에 두지 않는다는 결정 0001의
    화면 규칙과 같은 이유다.
    """

    query: SearchQuery
    target_label: str
    person: PersonSummary | None
    briefing: str
    sort: SortOrder
    hits: tuple[DetectionHit, ...]
    truncated: bool
    snapshot_lookup_failed: bool
