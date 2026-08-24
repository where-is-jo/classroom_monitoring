"""프레임 버퍼에서 최신 프레임을 꺼내 추론을 돌리는 소비자 루프.

stream worker를 import하지 않는다. 두 워커는 shared의 버퍼만 알고 서로를 모른다.
나중에 추론을 별도 프로세스로 떼어낼 때 고칠 곳이 버퍼 구현 하나로 좁혀진다.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from shared.frame_buffer import FrameBuffer
from shared.types import CapturedFrame

from .metrics import CONSECUTIVE_FAILURES, FRAMES_PROCESSED_TOTAL
from .processor import InferenceProcessor
from .types import EntryFaceObservationBatch, InferenceResult

logger = logging.getLogger(__name__)

ResultHandler = Callable[[CapturedFrame, InferenceResult], None]
EntryResultHandler = Callable[[CapturedFrame, EntryFaceObservationBatch], None]


class EntryProcessor(Protocol):
    def process(self, captured: CapturedFrame) -> EntryFaceObservationBatch: ...


@dataclass(frozen=True)
class ConsumerStats:
    """소비자 처리량 스냅샷. monitoring 지표로 내보낼 값이다."""

    processed: int
    failed: int


def log_result(captured: CapturedFrame, result: InferenceResult) -> None:
    """기본 결과 처리. 탐지 결과를 로그로 남긴다.

    **여기서 업무 의미를 붙이지 않는다.** "사람 1명, 신뢰도 0.87"까지가 이 워커의
    출력이고, "근무중"으로의 해석은 state worker 또는 fastapi의 일이다.
    """
    if not result.detections:
        logger.debug(
            "카메라 %s 프레임 %d: 탐지 없음", captured.camera_id, captured.sequence
        )
        return

    summary = ", ".join(
        f"{detection.class_name} {detection.confidence:.2f}"
        for detection in result.detections
    )
    logger.info(
        "카메라 %s 프레임 %d: %d건 — %s",
        captured.camera_id,
        captured.sequence,
        len(result.detections),
        summary,
    )


class InferenceConsumer:
    """버퍼가 비어 있으면 기다리고, 프레임이 들어오면 추론한다."""

    def __init__(
        self,
        *,
        frame_buffer: FrameBuffer,
        processor: InferenceProcessor,
        shutdown_event: threading.Event,
        poll_timeout_seconds: float = 0.5,
        max_consecutive_failures: int = 5,
        result_handler: ResultHandler = log_result,
        entry_processor: EntryProcessor | None = None,
        entry_camera_ids: frozenset[str] = frozenset(),
        entry_result_handler: EntryResultHandler | None = None,
    ) -> None:
        if bool(entry_camera_ids) != bool(entry_processor and entry_result_handler):
            raise ValueError(
                "입구 얼굴 카메라·processor·result handler는 함께 설정해야 합니다."
            )
        self._frame_buffer = frame_buffer
        self._processor = processor
        self._shutdown_event = shutdown_event
        self._poll_timeout_seconds = poll_timeout_seconds
        self._max_consecutive_failures = max_consecutive_failures
        self._result_handler = result_handler
        self._entry_processor = entry_processor
        self._entry_camera_ids = entry_camera_ids
        self._entry_result_handler = entry_result_handler

        self._processed = 0
        self._failed = 0
        self._consecutive_failures = 0

    @property
    def stats(self) -> ConsumerStats:
        return ConsumerStats(processed=self._processed, failed=self._failed)

    def run(self) -> None:
        logger.info("추론 소비자를 시작한다")
        try:
            while not self._shutdown_event.is_set():
                # timeout을 두는 이유는 버퍼가 계속 비어 있어도 종료 신호를
                # 확인할 수 있어야 하기 때문이다.
                captured = self._frame_buffer.get_latest(
                    timeout=self._poll_timeout_seconds
                )
                if captured is None:
                    continue
                self._process(captured)
        finally:
            logger.info(
                "추론 소비자를 종료한다 (처리 %d, 실패 %d, 버퍼 통계 %s)",
                self._processed,
                self._failed,
                self._frame_buffer.stats,
            )

    def _process(self, captured: CapturedFrame) -> None:
        try:
            if captured.camera_id in self._entry_camera_ids:
                assert self._entry_processor is not None
                entry_result = self._entry_processor.process(captured)
                result = None
            else:
                entry_result = None
                result = self._processor.process(captured.frame)
        except Exception:
            # 모델 호출은 프레임 형태·장치 상태·가중치 문제 등으로 여러 예외를 낸다.
            # 프레임 한 장 때문에 파이프라인 전체를 죽이지는 않되, 조용히 넘기지도
            # 않는다. 스택을 남기고, 연속으로 실패하면 멈춘다.
            self._failed += 1
            self._consecutive_failures += 1
            FRAMES_PROCESSED_TOTAL.labels(
                camera_id=captured.camera_id, result="failed"
            ).inc()
            # 한계에 닿기 전에 알아채려면 지금 몇 번째인지가 보여야 한다.
            CONSECUTIVE_FAILURES.set(self._consecutive_failures)
            logger.exception(
                "카메라 %s 프레임 %d 추론 실패 (연속 %d회)",
                captured.camera_id,
                captured.sequence,
                self._consecutive_failures,
            )
            if self._consecutive_failures >= self._max_consecutive_failures:
                logger.error(
                    "추론이 연속 %d회 실패해 파이프라인을 멈춘다. "
                    "모델 경로와 실행 장치를 확인한다.",
                    self._consecutive_failures,
                )
                self._shutdown_event.set()
            return

        self._consecutive_failures = 0
        CONSECUTIVE_FAILURES.set(0)
        self._processed += 1
        FRAMES_PROCESSED_TOTAL.labels(camera_id=captured.camera_id, result="ok").inc()
        if entry_result is not None:
            assert self._entry_result_handler is not None
            self._entry_result_handler(captured, entry_result)
        else:
            assert result is not None
            self._result_handler(captured, result)
