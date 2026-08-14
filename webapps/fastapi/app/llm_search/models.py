"""자연어 검색의 도메인 모델.

`SearchQuery`는 **검증을 통과한 뒤에만** 만들어진다. 이 타입의 값이 손에 있다는 것은
"LLM이 뭐라고 했든 저장소에 넣어도 되는 조건"이라는 뜻이다. 검증 전 상태를 담는
타입을 따로 두지 않는 이유도 그것이다 — 검증 전 값은 `dict`로만 존재하고
`planning.py` 밖으로 나가지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CameraChoice:
    """LLM에게 알려줄 카메라 하나.

    프롬프트에 실제 등록된 식별자를 넣어야 모델이 없는 강의실을 지어내지 않는다.
    `video_monitoring`의 `VideoStream`을 그대로 쓰지 않는 이유는 프롬프트 조립이
    순수 함수로 남아야 하기 때문이다. 필요한 세 값만 옮겨 담는다.
    """

    camera_id: str
    classroom_id: str
    label: str


@dataclass(frozen=True)
class SearchQuery:
    """검증된 검색 조건.

    시각은 항상 시각대를 가진 UTC이고 `from_at < to_at`이다. 저장소가 반개구간
    (`from_at <= x < to_at`)으로 조회하므로 두 값이 같으면 조용히 0건이 된다.

    `notes`는 사용자에게 그대로 보여줄 한국어 문장이다. 요청을 조정했으면
    (기간 절삭, limit 제한) 반드시 여기에 남긴다. **조용히 줄이지 않는다.**
    """

    camera_id: str | None
    classroom_id: str | None
    from_at: datetime
    to_at: datetime
    limit: int
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

    `snapshot_key`는 저장소에 실제로 있는 키이거나 `None`이다. 계산만 하고 존재를
    확인하지 않은 키는 여기 담지 않는다.
    """

    event_id: str
    camera_id: str
    resolved_classroom_id: str
    captured_at: datetime
    detection_count: int
    identified: tuple[IdentifiedStudent, ...]
    unidentified_count: int
    snapshot_key: str | None


@dataclass(frozen=True)
class SearchOutcome:
    """검색 한 번의 결과 전부.

    `truncated`와 `snapshot_lookup_failed`를 결과와 함께 돌려주는 이유는 화면이
    "없음"과 "못 봤음"을 구분해야 하기 때문이다. 둘을 같게 보여주면 운영자가
    데이터가 없다고 판단한다.
    """

    query: SearchQuery
    hits: tuple[DetectionHit, ...]
    truncated: bool
    snapshot_lookup_failed: bool
