"""stream worker와 inference worker를 한 프로세스에서 함께 돌린다.

수신은 카메라별 스레드가, 추론은 소비자 스레드가 맡고 둘은 프레임 버퍼로만
닿는다. 어느 쪽도 상대를 import하지 않는다.
"""

from __future__ import annotations

import logging
import threading

from inference.consumer import InferenceConsumer
from shared.frame_buffer import FrameBuffer
from stream.worker import StreamWorker

logger = logging.getLogger(__name__)

_CONSUMER_JOIN_TIMEOUT_SECONDS = 10.0


class PipelineRunner:
    """수신과 추론의 수명을 함께 관리한다."""

    def __init__(
        self,
        *,
        stream_worker: StreamWorker,
        consumer: InferenceConsumer,
        frame_buffer: FrameBuffer,
        shutdown_event: threading.Event,
        consumer_join_timeout_seconds: float = _CONSUMER_JOIN_TIMEOUT_SECONDS,
    ) -> None:
        self._stream_worker = stream_worker
        self._consumer = consumer
        self._frame_buffer = frame_buffer
        self._shutdown_event = shutdown_event
        self._consumer_join_timeout_seconds = consumer_join_timeout_seconds

    @property
    def frame_buffer(self) -> FrameBuffer:
        """조립 지점이 버퍼 지표를 등록할 때 쓴다.

        `build_runner`가 아니라 `main`에서 등록하는 이유는 전역 레지스트리에 같은
        collector를 두 번 넣을 수 없어서다. 조립 함수는 테스트가 여러 번 부른다.
        """
        return self._frame_buffer

    def request_shutdown(self) -> None:
        self._shutdown_event.set()
        # 버퍼에서 대기 중인 소비자를 즉시 깨운다. 이게 없으면 poll timeout만큼
        # 종료가 늦어진다.
        self._frame_buffer.close()

    def run(self) -> int:
        consumer_thread = threading.Thread(
            target=self._consumer.run, name="inference-consumer", daemon=True
        )
        consumer_thread.start()

        try:
            # 카메라 스레드를 띄우고 종료 신호까지 블로킹한다.
            self._stream_worker.run()
        finally:
            # 생산이 끝났음을 소비자에게 알린다. 버퍼를 닫지 않으면 소비자가
            # 오지 않을 프레임을 기다린다.
            self._shutdown_event.set()
            self._frame_buffer.close()
            consumer_thread.join(timeout=self._consumer_join_timeout_seconds)

            if consumer_thread.is_alive():
                # 추론 한 장이 join timeout보다 오래 걸리는 상태다. 감추지 않는다.
                logger.error(
                    "추론 소비자가 %.2f초 안에 끝나지 않았다. 진행 중인 추론이 "
                    "남아 있을 수 있다.",
                    self._consumer_join_timeout_seconds,
                )

            buffer_stats = self._frame_buffer.stats
            consumer_stats = self._consumer.stats
            logger.info(
                "파이프라인 종료 — 버퍼 accepted=%d dropped=%d skipped=%d, "
                "추론 processed=%d failed=%d",
                buffer_stats.accepted,
                buffer_stats.dropped,
                buffer_stats.skipped,
                consumer_stats.processed,
                consumer_stats.failed,
            )

        return 1 if self._consumer.stats.failed and not self._consumer.stats.processed else 0
