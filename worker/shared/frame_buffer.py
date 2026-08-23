"""stream worker와 inference worker 사이의 프레임 버퍼.

실시간 파이프라인이라 **밀린 프레임은 처리할 가치가 없다.** 추론이 수신보다
느릴 때 프레임을 쌓아두면 지연이 계속 커지고, 화면에 보이는 상태가 실제보다
과거가 된다. 그래서 이 버퍼는 가득 차면 오래된 프레임을 버리고, 소비자는
버퍼에 남은 것 중 **가장 최근 프레임만** 가져간다.

`queue.Queue`를 쓰지 않은 이유는 오래된 항목을 버리는 동작이 없기 때문이다.
`put_nowait`가 Full을 던지면 꺼내고 다시 넣어야 하는데, 생산자가 여럿이면
그 사이에 다른 생산자가 끼어들어 어느 프레임이 남는지 보장할 수 없다.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass

from .types import CapturedFrame


@dataclass(frozen=True)
class FrameBufferStats:
    """버퍼 처리량 스냅샷. monitoring 지표로 내보낼 값이다."""

    accepted: int
    """버퍼에 들어간 프레임 수."""

    dropped: int
    """자리를 만들기 위해 버린 오래된 프레임 수. 추론이 수신을 못 따라간 양이다."""

    consumed: int
    """소비자가 가져간 프레임 수."""

    skipped: int
    """가져갈 때 최신이 아니어서 건너뛴 프레임 수."""

    @property
    def discarded(self) -> int:
        """추론에 닿지 못한 전체 프레임 수."""
        return self.dropped + self.skipped


class FrameBuffer:
    """생산자 여럿과 소비자 여럿이 함께 쓸 수 있는, 최신 프레임 우선 버퍼.

    ``per_camera=True``이면 카메라마다 최신 프레임 한 장을 따로 보관한다. 여러
    카메라가 한 버퍼를 공유하는 pipeline에서는 이 모드를 써야 빠른 CCTV가 느린
    입구 카메라의 프레임을 계속 덮어쓰지 않는다. 소비자는 먼저 대기하기 시작한
    카메라부터 한 장씩 가져가므로 특정 카메라가 추론을 독점하지 않는다.
    """

    def __init__(self, *, maxsize: int = 1, per_camera: bool = False) -> None:
        if maxsize < 1:
            raise ValueError("버퍼 크기는 1 이상이어야 합니다.")

        self._maxsize = maxsize
        self._per_camera = per_camera
        self._frames: deque[CapturedFrame] = deque()
        self._camera_frames: dict[str, CapturedFrame] = {}
        self._camera_order: deque[str] = deque()
        self._condition = threading.Condition()
        self._is_closed = False

        self._accepted = 0
        self._dropped = 0
        self._consumed = 0
        self._skipped = 0

    @property
    def maxsize(self) -> int:
        return self._maxsize

    @property
    def is_closed(self) -> bool:
        with self._condition:
            return self._is_closed

    @property
    def stats(self) -> FrameBufferStats:
        with self._condition:
            return FrameBufferStats(
                accepted=self._accepted,
                dropped=self._dropped,
                consumed=self._consumed,
                skipped=self._skipped,
            )

    def __len__(self) -> int:
        with self._condition:
            return (
                len(self._camera_frames) if self._per_camera else len(self._frames)
            )

    def _has_frames(self) -> bool:
        return bool(self._camera_frames) if self._per_camera else bool(self._frames)

    def put(self, captured: CapturedFrame) -> bool:
        """프레임을 넣는다. 절대 블로킹하지 않는다.

        수신 루프가 추론을 기다리며 멈추면 카메라 버퍼에 지연이 쌓인다.
        버퍼가 가득 차면 가장 오래된 프레임을 버리고 새 프레임을 넣는다.

        반환값은 프레임이 버퍼에 들어갔는지다. 닫힌 버퍼에는 들어가지 않는다.
        무엇을 버렸는지는 `stats.dropped`로 확인한다.
        """
        with self._condition:
            if self._is_closed:
                return False

            if self._per_camera:
                camera_id = captured.camera_id
                if camera_id in self._camera_frames:
                    # 같은 카메라에서 아직 소비하지 않은 프레임은 최신 장면으로
                    # 교체한다. 순서는 유지해 빠른 카메라가 큐의 뒤로 계속 밀려나지
                    # 않게 한다.
                    self._camera_frames[camera_id] = captured
                    self._dropped += 1
                else:
                    if len(self._camera_frames) >= self._maxsize:
                        oldest_camera_id = self._camera_order.popleft()
                        del self._camera_frames[oldest_camera_id]
                        self._dropped += 1
                    self._camera_frames[camera_id] = captured
                    self._camera_order.append(camera_id)
                self._accepted += 1
                self._condition.notify()
                return True

            if len(self._frames) >= self._maxsize:
                self._frames.popleft()
                self._dropped += 1

            self._frames.append(captured)
            self._accepted += 1
            self._condition.notify()
            return True

    def get_latest(self, *, timeout: float | None = None) -> CapturedFrame | None:
        """가장 최근 프레임을 가져온다. 그보다 오래된 것은 버린다.

        `timeout`이 지나도 프레임이 없거나 버퍼가 닫혔으면 None을 돌려준다.
        소비자는 None을 종료 신호 확인 시점으로 쓴다.
        """
        with self._condition:
            is_ready = self._condition.wait_for(
                lambda: self._has_frames() or self._is_closed, timeout=timeout
            )
            if not is_ready or self._is_closed:
                return None

            if self._per_camera:
                camera_id = self._camera_order.popleft()
                latest = self._camera_frames.pop(camera_id)
                self._consumed += 1
                return latest

            self._skipped += len(self._frames) - 1
            latest = self._frames[-1]
            self._frames.clear()
            self._consumed += 1
            return latest

    def close(self) -> None:
        """버퍼를 닫고 기다리는 소비자를 모두 깨운다.

        남은 프레임은 버린다. 종료 중에 밀린 프레임을 추론해도 쓸 데가 없고,
        그만큼 종료가 늦어진다.
        """
        with self._condition:
            if self._is_closed:
                return
            self._is_closed = True
            self._frames.clear()
            self._camera_frames.clear()
            self._camera_order.clear()
            self._condition.notify_all()
