"""결과 핸들러 호출을 추론 소비자 스레드에서 떼어낸다.

## 왜 필요한가

추론 소비자는 프레임을 꺼내 모델을 부르고, 그 결과를 핸들러에 넘긴다. 그런데 그
핸들러 끝에 FastAPI HTTP 전송이 달려 있어서 **소비자 스레드가 네트워크 응답을
기다리는 동안 다음 프레임을 못 가져간다.**

실측(2026-08-25, dev):

| 구간 | 시간 |
| --- | --- |
| YOLO 추론 | 16.6ms (p50) |
| 얼굴 식별 호출 | 88.8ms |
| FastAPI `/health` (DB 안 씀) | 12.3ms |
| FastAPI 내부 API (DB 씀) | 198.7ms |
| MongoDB Atlas 왕복 1회 | 41.7ms |

탐지 이벤트 저장 한 건이 Atlas를 5~6번 순차로 왕복해 약 260ms가 든다. 그 결과
소비자가 초당 1.5장밖에 처리하지 못하고 들어온 프레임의 73%를 버렸다. **GPU는 그동안
1% 남짓만 쓰였다.** 병목이 계산이 아니라 대기였다.

## 무엇을 옮기고 무엇을 남기는가

**옮기는 것은 전송뿐이다.** ByteTrack과 신원 인계 coordinator는 프레임 순서에
의존하는 상태를 들고 있어서 소비자 스레드에 남는다. 순서가 섞이면 track이 깨진다.
조립 순서상 이 dispatcher는 그 둘보다 안쪽에 들어간다.

## 밀리면 최신으로 덮는다

카메라마다 아직 못 보낸 결과를 **하나만** 들고 있고, 새 결과가 오면 그것으로 덮는다.
프레임 버퍼의 per-camera 모드와 같은 판단이다 — 전송이 생산보다 느릴 때 옛 결과를
보내는 것은 지난 상태를 뒤늦게 반영하는 일이고, FastAPI는 최신 이벤트로 좌석을
판정하며 `event_id`로 멱등 처리한다. 덮은 수는 `coalesced`로, 큐가 넘쳐 버린 수는
`dropped`로 따로 센다.

## 보내는 주기에 하한을 둘 수 있다

`min_interval_seconds`를 주면 카메라 하나를 그 간격보다 자주 보내지 않는다. 추론
주기와 전송 주기를 나누기 위한 것이다 — ByteTrack은 초당 5장을 봐야 track이
유지되지만, 좌석 점유는 그만큼 자주 바뀌지 않는다(실측 `changed_count` 중앙값 0).
같은 것을 초당 다섯 번 보내면 받는 쪽만 그만큼 일한다.

**이 간격은 처리량을 늘리지 않는다.** 전송 스레드는 한 번에 한 건을 보내므로
도달량의 상한은 건당 처리 시간이 정한다. 간격이 줄이는 것은 만들어 놓고 버리는
낭비와 받는 쪽의 부하다.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from shared.types import CapturedFrame

from .metrics import (
    RESULT_DISPATCH_COALESCED_TOTAL,
    RESULT_DISPATCH_DROPPED_TOTAL,
    RESULT_DISPATCH_DURATION_SECONDS,
    RESULT_DISPATCH_FAILED_TOTAL,
    RESULT_DISPATCH_QUEUE_DEPTH,
)

logger = logging.getLogger(__name__)

__all__ = ["AsyncResultDispatcher", "DispatcherStats"]

ResultT = TypeVar("ResultT")

_DEFAULT_QUEUE_MAXSIZE = 32
_DEFAULT_CLOSE_TIMEOUT_SECONDS = 5.0
# 큐가 비어 있을 때 종료 신호를 확인하는 주기. 프레임 버퍼의 poll timeout과 같은 이유다.
_POLL_TIMEOUT_SECONDS = 0.5


@dataclass(frozen=True)
class DispatcherStats:
    """전송 처리량 스냅샷."""

    accepted: int
    """큐에 들어간 결과 수."""

    dispatched: int
    """핸들러가 실제로 처리한 결과 수."""

    dropped: int
    """큐가 가득 차 버린 결과 수."""

    coalesced: int
    """같은 카메라의 더 새로운 결과로 덮인 수. 버린 것과 원인이 다르다."""

    failed: int
    """핸들러가 예외로 끝난 횟수."""


class AsyncResultDispatcher(Generic[ResultT]):
    """결과를 큐에 넣고 즉시 돌아온다. 실제 호출은 전용 스레드가 한다.

    `__call__` 서명이 감싸는 핸들러와 같아서 조립에서 그대로 자리를 바꿔 끼울 수 있다.
    """

    def __init__(
        self,
        handler: Callable[[CapturedFrame, ResultT], None],
        *,
        channel: str,
        maxsize: int = _DEFAULT_QUEUE_MAXSIZE,
        min_interval_seconds: float = 0.0,
        close_timeout_seconds: float = _DEFAULT_CLOSE_TIMEOUT_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if maxsize < 1:
            raise ValueError("전송 큐 크기는 1 이상이어야 합니다.")
        if min_interval_seconds < 0:
            raise ValueError("최소 전송 간격은 0 이상이어야 합니다.")

        self._handler = handler
        self._channel = channel
        self._maxsize = maxsize
        self._min_interval_seconds = min_interval_seconds
        self._close_timeout_seconds = close_timeout_seconds
        self._monotonic = monotonic

        # **카메라마다 최신 결과 하나만 들고 있는다.** 프레임 버퍼와 같은 판단이다 —
        # 아직 못 보낸 결과가 있는데 새 결과가 오면, 옛것을 보내는 것은 지난 상태를
        # 뒤늦게 반영하는 일이다. 순서는 유지해 빠른 카메라가 느린 카메라의 차례를
        # 계속 뺏지 않게 한다.
        self._pending: dict[str, tuple[CapturedFrame, ResultT]] = {}
        self._order: deque[str] = deque()
        self._last_sent_at: dict[str, float] = {}
        self._condition = threading.Condition()
        self._is_closed = False

        self._accepted = 0
        self._dispatched = 0
        self._dropped = 0
        self._coalesced = 0
        self._failed = 0

        self._thread = threading.Thread(
            target=self._run, name=f"result-dispatch-{channel}", daemon=True
        )
        self._thread.start()

    @property
    def channel(self) -> str:
        return self._channel

    @property
    def stats(self) -> DispatcherStats:
        with self._condition:
            return DispatcherStats(
                accepted=self._accepted,
                dispatched=self._dispatched,
                dropped=self._dropped,
                coalesced=self._coalesced,
                failed=self._failed,
            )

    def __call__(self, captured: CapturedFrame, result: ResultT) -> None:
        """결과를 큐에 넣는다. **절대 블로킹하지 않는다.**

        같은 카메라의 아직 못 보낸 결과가 있으면 **최신 것으로 덮는다.** 그래야
        전송이 밀려도 보내는 값이 항상 지금 상태다. 덮은 수는 `coalesced`로 센다 —
        큐가 넘쳐 버린 것(`dropped`)과 원인이 달라 함께 세면 안 된다.

        닫힌 뒤에 들어온 결과는 버린다. 종료 중에 새 전송을 시작하면 그만큼 종료가
        늦어지고, 어차피 곧 사라질 상태다.
        """
        with self._condition:
            if self._is_closed:
                return
            camera_id = captured.camera_id
            if camera_id in self._pending:
                self._pending[camera_id] = (captured, result)
                self._coalesced += 1
                RESULT_DISPATCH_COALESCED_TOTAL.labels(channel=self._channel).inc()
            else:
                if len(self._pending) >= self._maxsize:
                    # 카메라 수가 큐 크기를 넘는 구성이다. 가장 오래 기다린 것을 버린다.
                    oldest = self._order.popleft()
                    del self._pending[oldest]
                    self._dropped += 1
                    RESULT_DISPATCH_DROPPED_TOTAL.labels(channel=self._channel).inc()
                self._pending[camera_id] = (captured, result)
                self._order.append(camera_id)
            self._accepted += 1
            RESULT_DISPATCH_QUEUE_DEPTH.labels(channel=self._channel).set(
                len(self._pending)
            )
            self._condition.notify()

    def close(self) -> None:
        """큐에 남은 것을 잠시 기다렸다가 전송 스레드를 멈춘다.

        기다리는 이유는 마지막 몇 건이 사라지면 종료 직전 상태가 화면에 반영되지
        않기 때문이다. 다만 상한을 둔다 — FastAPI가 죽어 있으면 영원히 못 끝낸다.
        """
        with self._condition:
            if self._is_closed:
                return
            self._is_closed = True
            self._condition.notify_all()

        self._thread.join(timeout=self._close_timeout_seconds)
        if self._thread.is_alive():
            logger.warning(
                "%s 전송 스레드가 %.1f초 안에 끝나지 않았다. 전송 중인 요청이 "
                "남아 있을 수 있다.",
                self._channel,
                self._close_timeout_seconds,
            )

    def _wait_seconds_for(self, camera_id: str) -> float:
        """그 카메라를 다시 보내기까지 남은 시간. 0이면 지금 보내도 된다."""
        if self._min_interval_seconds <= 0:
            return 0.0
        last = self._last_sent_at.get(camera_id)
        if last is None:
            return 0.0
        return max(0.0, self._min_interval_seconds - (self._monotonic() - last))

    def _take(self) -> tuple[CapturedFrame, ResultT] | None:
        with self._condition:
            is_ready = self._condition.wait_for(
                lambda: bool(self._pending) or self._is_closed,
                timeout=_POLL_TIMEOUT_SECONDS,
            )
            if not is_ready:
                return None
            if not self._pending:
                # 닫혔고 남은 것도 없다.
                return None

            camera_id = self._order[0]
            # **간격이 남았으면 기다린다.** 닫히는 중이면 남은 것을 바로 내보낸다 —
            # 종료를 간격만큼 늦출 이유가 없다.
            if not self._is_closed:
                remaining = self._wait_seconds_for(camera_id)
                if remaining > 0:
                    self._condition.wait(timeout=min(remaining, _POLL_TIMEOUT_SECONDS))
                    return None

            self._order.popleft()
            item = self._pending.pop(camera_id)
            self._last_sent_at[camera_id] = self._monotonic()
            RESULT_DISPATCH_QUEUE_DEPTH.labels(channel=self._channel).set(
                len(self._pending)
            )
            return item

    def _run(self) -> None:
        logger.info(
            "%s 결과 전송 스레드를 시작한다 (카메라당 최소 간격 %.2f초)",
            self._channel,
            self._min_interval_seconds,
        )
        try:
            while True:
                item = self._take()
                if item is None:
                    with self._condition:
                        # 닫혔고 큐도 비었으면 끝낸다. 닫히지 않았다면 timeout이거나
                        # 전송 간격을 기다리는 중이라 다시 돈다.
                        if self._is_closed and not self._pending:
                            return
                    continue
                self._dispatch(*item)
        finally:
            logger.info(
                "%s 결과 전송 스레드를 종료한다 (%s)", self._channel, self.stats
            )

    def _dispatch(self, captured: CapturedFrame, result: ResultT) -> None:
        with RESULT_DISPATCH_DURATION_SECONDS.labels(channel=self._channel).time():
            try:
                self._handler(captured, result)
            except Exception:
                # **여기서 예외를 새면 전송 스레드가 죽고 이후 결과가 전부 사라진다.**
                # 감싸는 핸들러들이 이미 실패를 삼키도록 만들어져 있지만, 이 스레드는
                # 그 계약을 믿지 않는다. 대신 남기고 계속 돈다.
                with self._condition:
                    self._failed += 1
                RESULT_DISPATCH_FAILED_TOTAL.labels(channel=self._channel).inc()
                logger.exception(
                    "카메라 %s 프레임 %d 결과 전송 실패",
                    captured.camera_id,
                    captured.sequence,
                )
                return
        with self._condition:
            self._dispatched += 1
