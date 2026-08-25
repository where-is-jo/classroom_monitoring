"""stream worker와 inference worker를 한 프로세스에서 함께 돌린다.

수신은 카메라별 스레드가, 추론은 소비자 스레드가 맡고 둘은 프레임 버퍼로만
닿는다. 어느 쪽도 상대를 import하지 않는다.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from typing import Protocol

from inference.consumer import InferenceConsumer
from shared.frame_buffer import FrameBuffer
from stream.worker import StreamWorker

logger = logging.getLogger(__name__)

_CONSUMER_JOIN_TIMEOUT_SECONDS = 10.0


class ResultDispatcher(Protocol):
    """전송 스레드를 들고 있는 것 중 runner가 실제로 쓰는 부분만 추린 것.

    `AsyncResultDispatcher`를 직접 import하지 않는 이유는 runner가 결과 종류를
    알 필요가 없기 때문이다. 탐지든 입구 관측이든 닫는 방법은 같다.
    """

    def close(self) -> None: ...


class PipelineRunner:
    """수신과 추론의 수명을 함께 관리한다."""

    def __init__(
        self,
        *,
        stream_worker: StreamWorker,
        consumer: InferenceConsumer,
        frame_buffer: FrameBuffer,
        shutdown_event: threading.Event,
        additional_consumers: Sequence[InferenceConsumer] = (),
        additional_frame_buffers: Sequence[FrameBuffer] = (),
        result_dispatchers: Sequence[ResultDispatcher] = (),
        consumer_join_timeout_seconds: float = _CONSUMER_JOIN_TIMEOUT_SECONDS,
    ) -> None:
        self._stream_worker = stream_worker
        self._consumer = consumer
        self._frame_buffer = frame_buffer
        self._consumers = (consumer, *additional_consumers)
        self._frame_buffers = (frame_buffer, *additional_frame_buffers)
        if len(self._consumers) != len(self._frame_buffers):
            raise ValueError("추론 소비자와 프레임 버퍼 수가 같아야 합니다.")
        self._shutdown_event = shutdown_event
        # 전송 스레드는 소비자보다 늦게 닫는다. 소비자가 마지막으로 넘긴 결과까지
        # 내보내야 종료 직전 상태가 화면에 반영된다.
        self._result_dispatchers = tuple(result_dispatchers)
        self._consumer_join_timeout_seconds = consumer_join_timeout_seconds

    @property
    def frame_buffer(self) -> FrameBuffer:
        """조립 지점이 버퍼 지표를 등록할 때 쓴다.

        `build_runner`가 아니라 `main`에서 등록하는 이유는 전역 레지스트리에 같은
        collector를 두 번 넣을 수 없어서다. 조립 함수는 테스트가 여러 번 부른다.
        """
        return self._frame_buffer

    @property
    def frame_buffers(self) -> tuple[FrameBuffer, ...]:
        return self._frame_buffers

    def request_shutdown(self) -> None:
        self._shutdown_event.set()
        # 버퍼에서 대기 중인 소비자를 즉시 깨운다. 이게 없으면 poll timeout만큼
        # 종료가 늦어진다.
        for frame_buffer in self._frame_buffers:
            frame_buffer.close()

    def run(self) -> int:
        consumer_threads = [
            threading.Thread(
                target=consumer.run,
                name=(
                    "inference-consumer"
                    if index == 0
                    else f"inference-consumer-{index + 1}"
                ),
                daemon=True,
            )
            for index, consumer in enumerate(self._consumers)
        ]
        for thread in consumer_threads:
            thread.start()

        try:
            # 카메라 스레드를 띄우고 종료 신호까지 블로킹한다.
            self._stream_worker.run()
        finally:
            # 생산이 끝났음을 소비자에게 알린다. 버퍼를 닫지 않으면 소비자가
            # 오지 않을 프레임을 기다린다.
            self._shutdown_event.set()
            for frame_buffer in self._frame_buffers:
                frame_buffer.close()
            for thread in consumer_threads:
                thread.join(timeout=self._consumer_join_timeout_seconds)
                if thread.is_alive():
                    # 추론 한 장이 join timeout보다 오래 걸리는 상태다. 감추지 않는다.
                    logger.error(
                        "추론 소비자 %s가 %.2f초 안에 끝나지 않았다. 진행 중인 "
                        "추론이 남아 있을 수 있다.",
                        thread.name,
                        self._consumer_join_timeout_seconds,
                    )

            # 소비자를 모두 세운 뒤에 닫는다. 먼저 닫으면 소비자가 마지막으로
            # 넘긴 결과가 큐에 들어가지도 못하고 사라진다.
            for dispatcher in self._result_dispatchers:
                dispatcher.close()

            buffer_stats = [frame_buffer.stats for frame_buffer in self._frame_buffers]
            consumer_stats = [consumer.stats for consumer in self._consumers]
            logger.info(
                "파이프라인 종료 — 버퍼 accepted=%d dropped=%d skipped=%d, "
                "추론 processed=%d failed=%d",
                sum(stats.accepted for stats in buffer_stats),
                sum(stats.dropped for stats in buffer_stats),
                sum(stats.skipped for stats in buffer_stats),
                sum(stats.processed for stats in consumer_stats),
                sum(stats.failed for stats in consumer_stats),
            )

        failed = sum(consumer.stats.failed for consumer in self._consumers)
        processed = sum(consumer.stats.processed for consumer in self._consumers)
        return 1 if failed and not processed else 0
