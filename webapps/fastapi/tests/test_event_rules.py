"""해석 규칙 단위 테스트.

순수 함수라 저장소도 서비스도 필요 없다. 규칙만 직접 검증한다.
서비스를 거친 동작은 test_event_service.py가 따로 확인한다.
"""

from __future__ import annotations

import pytest

from app.events.rules import classify_confidence


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [
        (1.0, "high"),
        (0.95, "high"),
        (0.80, "high"),
        (0.79, "medium"),
        (0.50, "medium"),
        (0.49, "low"),
        (0.0, "low"),
    ],
)
def test_신뢰도_등급은_임계값_경계를_포함해_정해진다(
    confidence: float, expected: str
) -> None:
    """경계값(0.80, 0.50)은 위쪽 등급에 포함된다."""
    level = classify_confidence(confidence, high_threshold=0.80, medium_threshold=0.50)

    assert level == expected


def test_임계값을_바꾸면_같은_신뢰도가_다른_등급이_된다() -> None:
    """판단 기준이 인자로 들어온다는 것을 확인한다.

    기준을 모듈 상수로 박으면 이 테스트가 불가능해진다.
    """
    strict = classify_confidence(0.7, high_threshold=0.9, medium_threshold=0.8)
    loose = classify_confidence(0.7, high_threshold=0.6, medium_threshold=0.3)

    assert strict == "low"
    assert loose == "high"


def test_두_임계값이_같으면_중간_등급이_사라진다() -> None:
    """설정에서 두 값을 같게 두는 경우의 동작을 고정한다.

    막지 않고 허용한다. 등급을 둘로 줄이려는 의도일 수 있고,
    규칙 함수가 설정값의 유효성까지 판단하지는 않는다.
    """
    assert classify_confidence(0.6, high_threshold=0.6, medium_threshold=0.6) == "high"
    assert classify_confidence(0.59, high_threshold=0.6, medium_threshold=0.6) == "low"
