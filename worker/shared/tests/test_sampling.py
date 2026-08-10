"""샘플링 판단을 고정 입력으로 검증한다."""

from __future__ import annotations

import pytest

from ..sampling import should_sample


@pytest.mark.parametrize(
    ("frame_index", "expected"),
    [(0, True), (1, False), (19, False), (20, True), (40, True), (41, False)],
)
def test_주기마다_한_장을_고른다(frame_index: int, expected: bool) -> None:
    assert should_sample(frame_index, 20) is expected


def test_주기가_1이면_모두_고른다() -> None:
    assert all(should_sample(index, 1) for index in range(5))


def test_주기가_0이면_거부한다() -> None:
    with pytest.raises(ValueError, match="1 이상"):
        should_sample(0, 0)
