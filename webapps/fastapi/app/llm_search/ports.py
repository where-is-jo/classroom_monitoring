"""자연어 검색의 프로세스 외부 I/O 경계.

여기 있는 것은 LLM 호출 하나뿐이다. 저장소·객체 저장소는 이미 다른 기능이 포트를
가지고 있어 그것을 그대로 쓴다. 결정 0001이 정한 네 경계 중 "추론 클라이언트"에
해당한다.

**포트는 원문 문자열만 돌려준다.** 파싱과 검증을 어댑터에 두면 이 기능의 본체인
검증을 HTTP 없이 시험할 수 없게 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class PlanPrompt:
    """모델에게 넘길 한 번의 요청.

    `now`를 함께 담는 이유는 대역 구현이 "오늘"을 계산해야 하기 때문이다. 서비스가
    한 번 구한 시각을 프롬프트와 대역이 함께 쓰므로, 자정 근처에서 지시문의 "오늘"과
    결과의 "오늘"이 달라지는 일이 없다.
    """

    system: str
    question: str
    now: datetime


class QueryPlanner(Protocol):
    """자연어 질문을 검색 계획 JSON으로 바꾸는 경계."""

    def plan(self, prompt: PlanPrompt) -> str:
        """모델 원문을 그대로 돌려준다. 규격 검증은 호출자가 한다."""
        ...
