"""프레임 버퍼의 드롭·최신 우선·종료 동작 검증."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

import numpy as np
import pytest

from ..frame_buffer import FrameBuffer
from ..types import CapturedFrame


def make_captured(sequence: int, *, camera_id: str = "camera-01") -> CapturedFrame:
    return CapturedFrame(
        camera_id=camera_id,
        frame=np.full((2, 2, 3), sequence % 256, dtype=np.uint8),
        captured_at=datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC),
        sequence=sequence,
    )


class TestPut:
    def test_빈_버퍼에_넣으면_들어간다(self) -> None:
        buffer = FrameBuffer(maxsize=2)

        assert buffer.put(make_captured(0)) is True
        assert len(buffer) == 1
        assert buffer.stats.accepted == 1
        assert buffer.stats.dropped == 0

    def test_가득_차면_가장_오래된_것을_버린다(self) -> None:
        buffer = FrameBuffer(maxsize=2)

        buffer.put(make_captured(0))
        buffer.put(make_captured(1))
        buffer.put(make_captured(2))

        assert len(buffer) == 2
        assert buffer.stats.dropped == 1
        assert buffer.get_latest(timeout=0).sequence == 2  # type: ignore[union-attr]

    def test_버린_뒤에도_넣기는_성공한다(self) -> None:
        buffer = FrameBuffer(maxsize=1)

        assert buffer.put(make_captured(0)) is True
        assert buffer.put(make_captured(1)) is True
        assert buffer.stats.dropped == 1

    def test_생산자가_소비자를_기다리지_않는다(self) -> None:
        """put이 블로킹하면 수신 루프가 추론 속도에 묶인다."""
        buffer = FrameBuffer(maxsize=1)

        for sequence in range(100):
            buffer.put(make_captured(sequence))

        assert buffer.stats.accepted == 100
        assert buffer.stats.dropped == 99
        assert len(buffer) == 1

    def test_닫힌_버퍼에는_들어가지_않는다(self) -> None:
        buffer = FrameBuffer(maxsize=1)
        buffer.close()

        assert buffer.put(make_captured(0)) is False
        assert buffer.stats.accepted == 0

    def test_크기가_0이면_거부한다(self) -> None:
        with pytest.raises(ValueError, match="1 이상"):
            FrameBuffer(maxsize=0)


class TestGetLatest:
    def test_가장_최근_프레임을_돌려준다(self) -> None:
        buffer = FrameBuffer(maxsize=5)
        for sequence in range(4):
            buffer.put(make_captured(sequence))

        latest = buffer.get_latest(timeout=0)

        assert latest is not None
        assert latest.sequence == 3

    def test_최신이_아닌_것은_건너뛰고_비운다(self) -> None:
        buffer = FrameBuffer(maxsize=5)
        for sequence in range(4):
            buffer.put(make_captured(sequence))

        buffer.get_latest(timeout=0)

        assert len(buffer) == 0
        assert buffer.stats.skipped == 3
        assert buffer.stats.consumed == 1

    def test_추론에_닿지_못한_수를_합산한다(self) -> None:
        buffer = FrameBuffer(maxsize=2)
        for sequence in range(5):
            buffer.put(make_captured(sequence))

        buffer.get_latest(timeout=0)

        # 5장 중 1장만 추론에 들어간다.
        assert buffer.stats.discarded == 4

    def test_비어_있으면_timeout_뒤에_None을_돌려준다(self) -> None:
        buffer = FrameBuffer(maxsize=1)

        assert buffer.get_latest(timeout=0.01) is None

    def test_기다리는_동안_들어온_프레임을_받는다(self) -> None:
        buffer = FrameBuffer(maxsize=1)
        received: list[CapturedFrame | None] = []
        started = threading.Event()

        def consume() -> None:
            started.set()
            received.append(buffer.get_latest(timeout=2.0))

        consumer = threading.Thread(target=consume)
        consumer.start()
        started.wait(timeout=1.0)
        buffer.put(make_captured(7))
        consumer.join(timeout=2.0)

        assert not consumer.is_alive()
        assert received[0] is not None
        assert received[0].sequence == 7


class TestClose:
    def test_기다리는_소비자를_깨운다(self) -> None:
        """닫아도 깨우지 않으면 종료가 timeout만큼 늦어진다."""
        buffer = FrameBuffer(maxsize=1)
        received: list[CapturedFrame | None] = []
        started = threading.Event()

        def consume() -> None:
            started.set()
            received.append(buffer.get_latest(timeout=10.0))

        consumer = threading.Thread(target=consume)
        consumer.start()
        started.wait(timeout=1.0)
        buffer.close()
        consumer.join(timeout=2.0)

        assert not consumer.is_alive(), "close가 소비자를 깨우지 못했다"
        assert received[0] is None

    def test_닫으면_남은_프레임을_버린다(self) -> None:
        buffer = FrameBuffer(maxsize=3)
        buffer.put(make_captured(0))
        buffer.put(make_captured(1))

        buffer.close()

        assert len(buffer) == 0
        assert buffer.get_latest(timeout=0) is None
        assert buffer.is_closed

    def test_두_번_닫아도_안전하다(self) -> None:
        buffer = FrameBuffer(maxsize=1)

        buffer.close()
        buffer.close()

        assert buffer.is_closed


class TestConcurrency:
    def test_생산자가_여럿이어도_카운터가_어긋나지_않는다(self) -> None:
        buffer = FrameBuffer(maxsize=1)
        producer_count = 4
        frames_each = 250

        def produce(camera_index: int) -> None:
            for sequence in range(frames_each):
                buffer.put(make_captured(sequence, camera_id=f"camera-{camera_index}"))

        producers = [
            threading.Thread(target=produce, args=(index,))
            for index in range(producer_count)
        ]
        for producer in producers:
            producer.start()
        for producer in producers:
            producer.join(timeout=10.0)

        stats = buffer.stats
        assert stats.accepted == producer_count * frames_each
        # 넣은 것은 버려졌거나 버퍼에 남아 있다. 둘 중 어디에도 없으면 유실이다.
        assert stats.dropped + len(buffer) == stats.accepted

    def test_소비자가_여럿이어도_한_프레임을_한_번만_가져간다(self) -> None:
        buffer = FrameBuffer(maxsize=1)
        buffer.put(make_captured(0))
        received: list[CapturedFrame] = []
        lock = threading.Lock()

        def consume() -> None:
            captured = buffer.get_latest(timeout=0.2)
            if captured is not None:
                with lock:
                    received.append(captured)

        consumers = [threading.Thread(target=consume) for _ in range(4)]
        for consumer in consumers:
            consumer.start()
        for consumer in consumers:
            consumer.join(timeout=5.0)

        assert len(received) == 1
        assert buffer.stats.consumed == 1
