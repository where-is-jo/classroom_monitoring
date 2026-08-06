"""탐지 결과를 업무 의미로 바꾸는 규칙.

데이터 흐름의 4단계 "해석"이 여기서 일어난다. deeplearning은 "사람 1명 탐지,
신뢰도 0.87"까지만 내놓고, 그것을 등급이나 상태로 읽는 것은 fastapi의 책임이다.

이 모듈의 함수는 **순수 함수**다. 외부 I/O가 없으므로 포트를 만들지 않는다
(ADR-0002: 순수 계산 로직은 포트 대상이 아니다). 임계값 같은 판단 기준은
설정에서 읽어 인자로 넘긴다. 모듈 안에 상수로 박지 않는다.

**Strategy 승격 후보 자리다.** 규칙이 늘어나면 여기가 먼저 커진다.
클래스 계층으로 바꾸는 조건은 ADR-0005의 판정 질문에 있고, 요약하면 셋이다.
변형이 2개 이상 실재하고, 선택이 설정·런타임 값으로 이뤄지고, 각 변형이 독립된
의존성이나 상태를 가질 때다. 지금은 변형이 하나뿐이라 순수 함수로 둔다.
`if` 세 줄을 클래스 세 개로 바꾸는 것은 읽기 쉬워지는 것이 아니다.
"""

from __future__ import annotations

ConfidenceLevel = str
"""신뢰도 등급. `"high"` / `"medium"` / `"low"` 중 하나다.

화면과 API가 이 문자열을 그대로 쓴다. 값을 바꾸면 템플릿의 배지 클래스
(`level--high` 등)와 API 응답이 함께 바뀌므로 깨는 변경이다.
"""


def classify_confidence(
    confidence: float,
    *,
    high_threshold: float,
    medium_threshold: float,
) -> ConfidenceLevel:
    """신뢰도 값을 등급으로 바꾼다.

    경계값은 등급에 포함한다. `high_threshold`가 0.80이면 0.80은 `"high"`다.

    이 판단을 템플릿이나 스키마에 두지 않는다. 기준이 바뀌어도 화면과 API를
    고치지 않기 위해서다.
    """
    if confidence >= high_threshold:
        return "high"
    if confidence >= medium_threshold:
        return "medium"
    return "low"
