"""LLM 없이 계약과 화면을 확인하기 위한 대역.

기본 모드가 이것이다. 얼굴 분석의 `SyntheticFaceAnalyzer`와 같은 역할이며, 개발과
테스트가 GPU 서버에 매이지 않게 한다.

**자연어를 해석하지 않는다.** 한국어 파싱을 여기에 흉내 내면 검색 규칙이 두 벌이
되고, 화면에서 본 결과가 어느 쪽에서 나온 것인지 알 수 없게 된다. 언제나 "오늘
하루 전체, 대상 지정 없음"을 낸다 — 계약이 지켜지는지 보여주는 것이 이 대역의 일이다.

내일 00:00까지를 요청하지만 실제 조회 구간의 끝은 "지금"이 된다. `planning.py`가
아직 오지 않은 시각을 잘라 내기 때문이며, 미래에는 탐지가 없으므로 결과는 같다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from ..ports import PlanPrompt
from ..prompts import KST


class StubQueryPlanner:
    """질문과 무관하게 "오늘 하루" 계획을 돌려준다."""

    def plan(self, prompt: PlanPrompt) -> str:
        # 하루의 경계는 사용자가 쓰는 시각(KST) 기준이다. UTC로 자르면 오전 9시에
        # 날짜가 바뀌어 "오늘"이 어제 오전까지를 포함하게 된다.
        start_kst = prompt.now.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)
        end_kst = start_kst + timedelta(days=1)
        return json.dumps(
            {
                "intent": "detection_search",
                "camera_id": None,
                "classroom_id": None,
                "from": _to_kst_text(start_kst),
                "to": _to_kst_text(end_kst),
            }
        )


def _to_kst_text(moment: datetime) -> str:
    # 프롬프트가 모델에게 요구하는 형식과 같다 — 한국 시각 + "+09:00". 대역만 UTC로
    # 답하면 "모델이 낸 값이 UTC로 정규화되는가"가 검증되지 않는다.
    return moment.astimezone(KST).strftime("%Y-%m-%dT%H:%M:%S+09:00")
