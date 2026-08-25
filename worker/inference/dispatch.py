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

## 밀리면 버린다

큐가 가득 차면 **가장 오래된 것을 버린다.** 프레임 버퍼와 같은 판단이다 — 실시간
파이프라인에서 밀린 결과는 이미 지난 상태이고, FastAPI는 최신 이벤트로 좌석을
판정한다. 버린 수는 지표로 남기므로 조용히 사라지지 않는다.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from shared.types import CapturedFrame

from .metrics import (
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
        close_timeout_seconds: float = _DEFAULT_CLOSE_TIMEOUT_SECONDS,
    ) -> None:
        if maxsize < 1:
            raise ValueError("전송 큐 크기는 1 이상이어야 합니다.")

        self._handler = handler
        self._channel = channel
        self._maxsize = maxsize
        self._close_timeout_seconds = close_timeout_seconds

        self._queue: deque[tuple[CapturedFrame, ResultT]] = deque()
        self._condition = threading.Condition()
        self._is_closed = False

        self._accepted = 0
        self._dispatched = 0
        self._dropped = 0
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
                failed=self._failed,
            )

    def __call__(self, captured: CapturedFrame, result: ResultT) -> None:
        """결과를 큐에 넣는다. **절대 블로킹하지 않는다.**

        닫힌 뒤에 들어온 결과는 버린다. 종료 중에 새 전송을 시작하면 그만큼 종료가
        늦어지고, 어차피 곧 사라질 상태다.
        """
        with self._condition:
            if self._is_closed:
                return
            if len(self._queue) >= self._maxsize:
                self._queue.popleft()
                self._dropped += 1
                RESULT_DISPATCH_DROPPED_TOTAL.labels(channel=self._channel).inc()
            self._queue.append((captured, result))
            self._accepted += 1
            RESULT_DISPATCH_QUEUE_DEPTH.labels(channel=self._channel).set(
                len(self._queue)
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

    def _take(self) -> tuple[CapturedFrame, ResultT] | None:
        with self._condition:
            is_ready = self._condition.wait_for(
                lambda: bool(self._queue) or self._is_closed,
                timeout=_POLL_TIMEOUT_SECONDS,
            )
            if not is_ready:
                return None
            if not self._queue:
                # 닫혔고 남은 것도 없다.
                return None
            item = self._queue.popleft()
            RESULT_DISPATCH_QUEUE_DEPTH.labels(channel=self._channel).set(
                len(self._queue)
            )
            return item

    def _run(self) -> None:
        logger.info("%s 결과 전송 스레드를 시작한다", self._channel)
        try:
            while True:
                item = self._take()
                if item is None:
                    with self._condition:
                        # 닫혔고 큐도 비었으면 끝낸다. 닫히지 않았다면 timeout이라
                        # 다시 기다린다.
                        if self._is_closed and not self._queue:
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
