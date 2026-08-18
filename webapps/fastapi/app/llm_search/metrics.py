"""자연어 검색이 노출하는 Prometheus 지표.

정의를 한곳에 모으고, 계측은 이름 있는 함수로만 부른다. 서비스 본문에
`labels(...).observe(...)`가 흩어지면 무엇을 재고 있는지 로직을 읽어야 알 수 있다.

## 왜 이 기능에 지표가 필요한가

이 기능에는 **조용히 나빠지는 경로가 여러 개** 있다.

- `service._plan`은 모델이 규격을 어기면 **한 번 더 물어본다.** 사용자에게는
  "조금 느리네"로만 보이는데, 첫 시도 실패율이 올라가면 지연과 GPU 사용량이
  두 배가 된다. 모델을 바꿀지 프롬프트를 고칠지 판단하려면 이 숫자가 있어야 한다.
- `adapters/llama_planner.py`는 `json_schema`를 모르는 빌드를 만나면 `json_object`로
  낮춰 다시 보낸다. 매 요청이 4xx를 한 번 맞고 재전송돼도 기능은 정상으로 보인다.
- llama-server는 추론 워커와 **GPU를 나눠 쓴다.** 검색이 몰려 탐지가 느려지는지는
  두 서비스의 지연을 함께 봐야 알 수 있다.

## 지표를 둘로만 나눈 이유

재시도율을 별도 Counter로 두지 않는다. Histogram이 이미 `_count`를 label 조합마다
내보내므로 `classroom_monitoring_llm_plan_duration_seconds_count`가 곧 시도 횟수다.
같은 값을 두 지표로 세면 어긋날 수 있다.

## label 값 종류 수 (고카디널리티 점검)

| 지표 | label | 예상 값 수 |
| --- | --- | --- |
| `llm_plan_duration_seconds` | `attempt`, `outcome` | 2 * 3 = 6 |
| `llm_search_duration_seconds` | `outcome` | 3 |
| `llm_schema_fallback_total` | 없음 | 1 |
| `llm_search_truncated_total` | 없음 | 1 |

**질문 원문과 그 해시를 label로 쓰지 않는다.** 값이 무한히 늘어나고, 질문에는
사람이 찾는 대상이 담긴다. 개별 질문을 되짚어야 하면 지표가 아니라 로그를 본다
(`service._attempt`가 원문을 debug 로그로 남긴다).
"""

from __future__ import annotations

import time
from typing import Literal

from prometheus_client import Counter, Histogram

from ..shared.metrics import METRIC_PREFIX

__all__ = [
    "PlanOutcome",
    "record_plan_attempt",
    "record_schema_fallback",
    "record_search",
    "record_search_truncated",
]

PlanOutcome = Literal["success", "invalid", "unavailable"]
"""한 번의 시도가 어떻게 끝났는가.

- `success` — 검증까지 통과한 계획을 얻었다
- `invalid` — 모델이 답은 냈지만 규격을 벗어났다. **질문을 고치면 해결될 수 있다**
- `unavailable` — 모델에게 물어보지도 못했다. 질문을 고쳐도 소용없다

세 상태를 코드에서 구분해 둔 이유(`errors.py`)가 지표에서도 그대로 필요하다.
합쳐 놓으면 "사용자가 질문을 다시 써야 하는가"와 "관리자를 불러야 하는가"를
대시보드에서 구분할 수 없다.
"""

# 버킷 상한이 60초인 이유는 타임아웃 설정 상한(LLM_SEARCH_TIMEOUT_SECONDS, 최대 120)
# 때문이다. 기본값 20초 부근을 촘촘히 보고 그 위는 "너무 느림"으로 뭉뚱그린다.
_LLM_BUCKETS = (0.5, 1.0, 2.0, 5.0, 10.0, 15.0, 20.0, 30.0, 60.0)

_PLAN_DURATION_SECONDS = Histogram(
    f"{METRIC_PREFIX}llm_plan_duration_seconds",
    "질문을 검색 조건으로 바꾸는 한 번의 시도에 걸린 시간",
    labelnames=("attempt", "outcome"),
    buckets=_LLM_BUCKETS,
)

# 계획 생성 + 탐지 조회 + 스냅샷 확인까지 **사용자가 실제로 기다린 시간**이다.
# 계획 지연과 함께 보면 느린 쪽이 LLM인지 저장소인지 갈린다.
_SEARCH_DURATION_SECONDS = Histogram(
    f"{METRIC_PREFIX}llm_search_duration_seconds",
    "자연어 검색 한 건을 끝내는 데 걸린 시간. 실패로 끝난 것도 포함한다",
    labelnames=("outcome",),
    buckets=_LLM_BUCKETS,
)

# llama-server가 json_schema 요청을 거절해 json_object로 낮춘 횟수.
# 상시 발동 중이면 생성 단계에서 구조를 강제하지 못하고 있다는 뜻이라,
# 저양자화 모델에서 규격 위반이 늘어난다.
_SCHEMA_FALLBACK_TOTAL = Counter(
    f"{METRIC_PREFIX}llm_schema_fallback_total",
    "llama-server가 json_schema를 거절해 json_object로 낮춘 횟수",
)

# 조회 상한(LLM_SEARCH_SCAN_LIMIT)에 걸려 결과 일부가 빠진 횟수.
# 계속 늘면 상한값이 실제 이벤트 양에 비해 작다는 신호다.
_SEARCH_TRUNCATED_TOTAL = Counter(
    f"{METRIC_PREFIX}llm_search_truncated_total",
    "조회 상한에 걸려 결과가 잘린 검색 건수",
)


def record_plan_attempt(*, retry: bool, outcome: PlanOutcome, started_at: float) -> None:
    """계획 생성 시도 한 번을 기록한다. `started_at`은 `time.perf_counter()` 값이다."""
    _PLAN_DURATION_SECONDS.labels(attempt="retry" if retry else "first", outcome=outcome).observe(
        time.perf_counter() - started_at
    )


def record_search(*, outcome: PlanOutcome, started_at: float) -> None:
    """검색 한 건을 기록한다. 실패로 끝난 것도 남긴다 — 기다린 시간은 같다."""
    _SEARCH_DURATION_SECONDS.labels(outcome=outcome).observe(time.perf_counter() - started_at)


def record_schema_fallback() -> None:
    _SCHEMA_FALLBACK_TOTAL.inc()


def record_search_truncated() -> None:
    _SEARCH_TRUNCATED_TOTAL.inc()
