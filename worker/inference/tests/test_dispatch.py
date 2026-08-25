"""결과 전송 분리(`AsyncResultDispatcher`)의 계약.

실제 네트워크 없이 돈다. 확인하는 것은 넷이다 — 호출자를 막지 않는가, 밀리면
오래된 것을 버리는가, 핸들러가 터져도 스레드가 사는가, 닫을 때 남은 것을 내보내는가.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

import numpy as np
import pytest
from shared.types import CapturedFrame

from inference.dispatch import AsyncResultDispatcher
from inference.types import Detection, InferenceResult


def _captured(sequence: int = 0, camera_id: str = "camera-01") -> CapturedFrame:
    return CapturedFrame(
        camera_id=camera_id,
        frame=np.zeros((4, 4, 3), dtype=np.uint8),
        captured_at=datetime(2026, 8, 25, 6, 0, sequence % 60, tzinfo=UTC),
        sequence=sequence,
    )


def _result(count: int = 0) -> InferenceResult:
    detections = tuple(
        Detection(class_id=0, class_name="person", confidence=0.9, bbox=(0, 0, 1, 1))
        for _ in range(count)
    )
    return InferenceResult(frame_shape=(4, 4, 3), detections=detections)


def _wait_until(predicate: object, *, timeout: float = 5.0) -> bool:
    """조건이 참이 될 때까지 기다린다. 전송이 다른 스레드에서 일어나기 때문이다."""
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():  # type: ignore[operator]
            return True
        time.sleep(0.005)
    return False


def test_호출자를_막지_않고_전송_스레드가_처리한다() -> None:
    released = threading.Event()
    handled: list[int] = []

    def slow_handler(captured: CapturedFrame, _result: InferenceResult) -> None:
        released.wait(timeout=5.0)
        handled.append(captured.sequence)

    dispatcher: AsyncResultDispatcher[InferenceResult] = AsyncResultDispatcher(
        slow_handler, channel="test"
    )
    try:
        started_at = time.perf_counter()
        dispatcher(_captured(1), _result())
        elapsed = time.perf_counter() - started_at

        # 핸들러가 아직 붙잡혀 있는데도 호출자는 이미 돌아와 있어야 한다.
        assert elapsed < 0.5
        assert handled == []

        released.set()
        assert _wait_until(lambda: handled == [1])
    finally:
        released.set()
        dispatcher.close()


def test_큐가_가득_차면_오래된_것부터_버린다() -> None:
    release = threading.Event()
    started = threading.Event()
    handled: list[int] = []

    def blocking_handler(captured: CapturedFrame, _result: InferenceResult) -> None:
        started.set()
        release.wait(timeout=5.0)
        handled.append(captured.sequence)

    dispatcher: AsyncResultDispatcher[InferenceResult] = AsyncResultDispatcher(
        blocking_handler, channel="test", maxsize=2
    )
    try:
        # 첫 건은 스레드가 바로 집어가 붙잡히므로, 큐에 쌓이는 것은 그다음부터다.
        # **핸들러가 실제로 시작한 것을 확인하고 넣어야** 큐 상태가 정해진다.
        dispatcher(_captured(1), _result())
        assert started.wait(timeout=5.0)
        for sequence in (2, 3, 4, 5):
            dispatcher(_captured(sequence), _result())

        stats = dispatcher.stats
        assert stats.accepted == 5
        # maxsize=2를 넘긴 만큼만 버린다.
        assert stats.dropped == 2

        release.set()
        assert _wait_until(lambda: len(handled) == 3)
        # 버려진 것은 오래된 쪽(2, 3)이고 최신이 남는다.
        assert handled == [1, 4, 5]
    finally:
        release.set()
        dispatcher.close()


def test_핸들러가_예외를_던져도_다음_결과를_계속_보낸다() -> None:
    handled: list[int] = []

    def flaky_handler(captured: CapturedFrame, _result: InferenceResult) -> None:
        if captured.sequence == 1:
            raise RuntimeError("전송 실패")
        handled.append(captured.sequence)

    dispatcher: AsyncResultDispatcher[InferenceResult] = AsyncResultDispatcher(
        flaky_handler, channel="test"
    )
    try:
        dispatcher(_captured(1), _result())
        dispatcher(_captured(2), _result())

        assert _wait_until(lambda: handled == [2])
        assert dispatcher.stats.failed == 1
        # 실패한 건은 dispatched로 세지 않는다.
        assert dispatcher.stats.dispatched == 1
    finally:
        dispatcher.close()


def test_닫을_때_큐에_남은_것을_내보낸다() -> None:
    handled: list[int] = []

    def handler(captured: CapturedFrame, _result: InferenceResult) -> None:
        time.sleep(0.01)
        handled.append(captured.sequence)

    dispatcher: AsyncResultDispatcher[InferenceResult] = AsyncResultDispatcher(
        handler, channel="test", maxsize=16
    )
    for sequence in range(5):
        dispatcher(_captured(sequence), _result())

    dispatcher.close()

    # 종료 직전 상태가 사라지면 화면이 실제와 어긋난 채로 남는다.
    assert handled == [0, 1, 2, 3, 4]


def test_닫은_뒤에_들어온_결과는_받지_않는다() -> None:
    handled: list[int] = []

    def handler(captured: CapturedFrame, _result: InferenceResult) -> None:
        handled.append(captured.sequence)

    dispatcher: AsyncResultDispatcher[InferenceResult] = AsyncResultDispatcher(
        handler, channel="test"
    )
    dispatcher.close()

    dispatcher(_captured(9), _result())

    assert handled == []
    assert dispatcher.stats.accepted == 0


def test_큐_크기는_1_이상이어야_한다() -> None:
    def handler(_captured: CapturedFrame, _result: InferenceResult) -> None:
        return None

    with pytest.raises(ValueError, match="1 이상"):
        AsyncResultDispatcher(handler, channel="test", maxsize=0)
