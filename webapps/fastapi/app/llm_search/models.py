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
