"""결과 전송 분리(`AsyncResultDispatcher`)의 계약.

실제 네트워크 없이 돈다. 확인하는 것은 다섯이다 — 호출자를 막지 않는가, 밀리면
카메라별로 최신을 남기는가, 카메라가 큐 크기를 넘으면 버리는가, 핸들러가 터져도
스레드가 사는가, 보내는 주기에 하한을 지키는가.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
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
        captured_at=datetime(2026, 8, 26, 6, 0, sequence % 60, tzinfo=UTC),
        sequence=sequence,
    )


def _result(count: int = 0) -> InferenceResult:
    detections = tuple(
        Detection(class_id=0, class_name="person", confidence=0.9, bbox=(0, 0, 1, 1))
        for _ in range(count)
    )
    return InferenceResult(frame_shape=(4, 4, 3), detections=detections)


def _wait_until(predicate: Callable[[], bool], *, timeout: float = 5.0) -> bool:
    """조건이 참이 될 때까지 기다린다. 전송이 다른 스레드에서 일어나기 때문이다."""
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
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


def test_같은_카메라는_최신_결과로_덮인다() -> None:
    release = threading.Event()
    started = threading.Event()
    handled: list[int] = []

    def blocking_handler(captured: CapturedFrame, _result: InferenceResult) -> None:
        started.set()
        release.wait(timeout=5.0)
        handled.append(captured.sequence)

    dispatcher: AsyncResultDispatcher[InferenceResult] = AsyncResultDispatcher(
        blocking_handler, channel="test"
    )
    try:
        # 첫 건은 스레드가 바로 집어가 붙잡힌다. 그 뒤에 넣는 것들이 큐에서 겹친다.
        dispatcher(_captured(1), _result())
        assert started.wait(timeout=5.0)
        for sequence in (2, 3, 4, 5):
            dispatcher(_captured(sequence), _result())

        stats = dispatcher.stats
        assert stats.accepted == 5
        # 같은 카메라라 큐에는 항상 한 건만 남는다. 겹친 3건은 덮인 것이지 버린 게 아니다.
        assert stats.coalesced == 3
        assert stats.dropped == 0

        release.set()
        # **보내는 것은 가장 최신(5)이다.** 밀린 옛 상태를 뒤늦게 보내지 않는다.
        assert _wait_until(lambda: handled == [1, 5])
    finally:
        release.set()
        dispatcher.close()


def test_카메라가_큐_크기를_넘으면_오래_기다린_것을_버린다() -> None:
    release = threading.Event()
    started = threading.Event()
    handled: list[str] = []

    def blocking_handler(captured: CapturedFrame, _result: InferenceResult) -> None:
        started.set()
        release.wait(timeout=5.0)
        handled.append(captured.camera_id)

    dispatcher: AsyncResultDispatcher[InferenceResult] = AsyncResultDispatcher(
        blocking_handler, channel="test", maxsize=2
    )
    try:
        dispatcher(_captured(1, "cam-a"), _result())
        assert started.wait(timeout=5.0)
        for camera_id in ("cam-b", "cam-c", "cam-d"):
            dispatcher(_captured(1, camera_id), _result())

        stats = dispatcher.stats
        # maxsize=2를 넘긴 만큼만 버린다. 서로 다른 카메라라 덮이지는 않는다.
        assert stats.dropped == 1
        assert stats.coalesced == 0

        release.set()
        assert _wait_until(lambda: len(handled) == 3)
        # 가장 오래 기다린 cam-b가 밀려났다.
        assert handled == ["cam-a", "cam-c", "cam-d"]
    finally:
        release.set()
        dispatcher.close()


def test_카메라마다_최소_간격을_지킨다() -> None:
    now = [0.0]
    handled: list[int] = []

    def handler(captured: CapturedFrame, _result: InferenceResult) -> None:
        handled.append(captured.sequence)

    dispatcher: AsyncResultDispatcher[InferenceResult] = AsyncResultDispatcher(
        handler,
        channel="test",
        min_interval_seconds=10.0,
        monotonic=lambda: now[0],
    )
    try:
        dispatcher(_captured(1), _result())
        assert _wait_until(lambda: handled == [1])

        # 간격이 지나지 않았다. 큐에 남아 있어야 하고 보내지 않아야 한다.
        dispatcher(_captured(2), _result())
        assert not _wait_until(lambda: len(handled) > 1, timeout=1.0)
        assert dispatcher.stats.accepted == 2

        # 그동안 더 새로운 것이 오면 덮인다 — 기다렸다가 옛것을 보내지 않는다.
        dispatcher(_captured(3), _result())
        assert dispatcher.stats.coalesced == 1

        now[0] = 20.0  # 간격이 지났다
        assert _wait_until(lambda: handled == [1, 3])
    finally:
        dispatcher.close()


def test_다른_카메라는_서로의_간격에_막히지_않는다() -> None:
    now = [0.0]
    handled: list[str] = []

    def handler(captured: CapturedFrame, _result: InferenceResult) -> None:
        handled.append(captured.camera_id)

    dispatcher: AsyncResultDispatcher[InferenceResult] = AsyncResultDispatcher(
        handler,
        channel="test",
        min_interval_seconds=10.0,
        monotonic=lambda: now[0],
    )
    try:
        dispatcher(_captured(1, "cam-a"), _result())
        dispatcher(_captured(1, "cam-b"), _result())
        # 간격은 카메라마다 따로 센다. 처음 보내는 카메라는 기다리지 않는다.
        assert _wait_until(lambda: sorted(handled) == ["cam-a", "cam-b"])
    finally:
        dispatcher.close()


def test_핸들러가_예외를_던져도_다음_결과를_계속_보낸다() -> None:
    handled: list[str] = []

    def flaky_handler(captured: CapturedFrame, _result: InferenceResult) -> None:
        if captured.camera_id == "cam-bad":
            raise RuntimeError("전송 실패")
        handled.append(captured.camera_id)

    dispatcher: AsyncResultDispatcher[InferenceResult] = AsyncResultDispatcher(
        flaky_handler, channel="test"
    )
    try:
        dispatcher(_captured(1, "cam-bad"), _result())
        dispatcher(_captured(1, "cam-good"), _result())

        assert _wait_until(lambda: handled == ["cam-good"])
        assert dispatcher.stats.failed == 1
        # 실패한 건은 dispatched로 세지 않는다.
        assert dispatcher.stats.dispatched == 1
    finally:
        dispatcher.close()


def test_닫을_때_큐에_남은_것을_내보낸다() -> None:
    handled: list[str] = []

    def handler(captured: CapturedFrame, _result: InferenceResult) -> None:
        time.sleep(0.01)
        handled.append(captured.camera_id)

    dispatcher: AsyncResultDispatcher[InferenceResult] = AsyncResultDispatcher(
        handler, channel="test", maxsize=16, min_interval_seconds=30.0
    )
    for index in range(5):
        dispatcher(_captured(index, f"cam-{index}"), _result())

    dispatcher.close()

    # 종료 직전 상태가 사라지면 화면이 실제와 어긋난 채로 남는다.
    # **닫는 중에는 최소 간격을 기다리지 않는다** — 종료를 늦출 이유가 없다.
    assert sorted(handled) == [f"cam-{index}" for index in range(5)]


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


def test_큐_크기와_간격은_음수일_수_없다() -> None:
    def handler(_captured: CapturedFrame, _result: InferenceResult) -> None:
        return None

    with pytest.raises(ValueError, match="1 이상"):
        AsyncResultDispatcher(handler, channel="test", maxsize=0)
    with pytest.raises(ValueError, match="0 이상"):
        AsyncResultDispatcher(handler, channel="test", min_interval_seconds=-1.0)
