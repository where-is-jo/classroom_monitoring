"""서비스 계층 단위 테스트.

FastAPI도 실제 저장소도 쓰지 않는다.
"""

from __future__ import annotations

import pytest

from app.events.service import EventService
from app.shared.errors import EventNotFoundError

from .conftest import FakeEventRepository, make_event


def test_목록은_페이지와_전체건수를_함께_반환한다(service: EventService) -> None:
    page = service.list_events(limit=2, offset=0)

    assert len(page.items) == 2
    assert page.total == 5


def test_offset이_전체를_넘으면_빈_목록을_반환한다(service: EventService) -> None:
    page = service.list_events(limit=10, offset=100)

    assert page.items == []
    assert page.total == 5


def test_없는_이벤트를_조회하면_예외가_발생한다(service: EventService) -> None:
    with pytest.raises(EventNotFoundError):
        service.get_event("evt-does-not-exist")


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [
        (0.95, "high"),
        (0.80, "high"),
        (0.79, "medium"),
        (0.50, "medium"),
        (0.49, "low"),
        (0.10, "low"),
    ],
)
def test_신뢰도_등급은_임계값_기준으로_정해진다(confidence: float, expected: str) -> None:
    repository = FakeEventRepository([make_event("evt-test-001", confidence=confidence)])
    service = EventService(
        repository,
        high_confidence_threshold=0.80,
        medium_confidence_threshold=0.50,
    )

    summary = service.get_event("evt-test-001")

    assert summary.confidence_level == expected


def test_임계값이_바뀌면_등급도_바뀐다() -> None:
    """임계값이 설정으로 주입된다는 것을 확인한다."""
    repository = FakeEventRepository([make_event("evt-test-001", confidence=0.7)])

    strict = EventService(
        repository, high_confidence_threshold=0.9, medium_confidence_threshold=0.8
    )
    loose = EventService(repository, high_confidence_threshold=0.6, medium_confidence_threshold=0.3)

    assert strict.get_event("evt-test-001").confidence_level == "low"
    assert loose.get_event("evt-test-001").confidence_level == "high"
