"""테스트 대역. 실제 장비 없이 연결 상태 전이를 검증하기 위한 것이다."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from ..camera_reader import Frame


def make_frame(width: int = 4, height: int = 3) -> Frame:
    return np.zeros((height, width, 3), dtype=np.uint8)


class FakeCapture:
    """VideoCaptureLike 대역.

    `open_results`는 isOpened()가 돌려줄 값을 순서대로, `read_results`는 read()가
    돌려줄 성공 여부를 순서대로 정한다. 목록이 끝나면 마지막 값을 계속 쓴다.
    """

    def __init__(
        self,
        *,
        is_open: bool = True,
        read_results: list[bool] | None = None,
    ) -> None:
        self._is_open = is_open
        self._read_results = read_results or [True]
        self._read_index = 0
        self.released = False

    def isOpened(self) -> bool:
        return self._is_open

    def read(self) -> tuple[bool, Frame | None]:
        index = min(self._read_index, len(self._read_results) - 1)
        self._read_index += 1
        if self._read_results[index]:
            return True, make_frame()
        return False, None

    def release(self) -> None:
        self.released = True

    def set(self, prop_id: int, value: float) -> bool:
        return True


class FakeCaptureFactory:
    """호출될 때마다 미리 정한 FakeCapture를 내준다. 재연결 횟수를 셀 수 있다."""

    def __init__(self, captures: list[FakeCapture]) -> None:
        self._captures = captures
        self.call_count = 0

    def __call__(self, rtsp_url: str) -> FakeCapture:
        index = min(self.call_count, len(self._captures) - 1)
        self.call_count += 1
        return self._captures[index]


class RecordingSleep:
    """time.sleep 대역. 테스트가 실제로 기다리지 않게 한다."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture
def sleep_spy() -> RecordingSleep:
    return RecordingSleep()


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"
