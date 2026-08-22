"""탐지 결과를 FastAPI 내부 API로 전송하는 핸들러.

`InferenceConsumer`의 `result_handler` 자리에 들어가 `ResultHandler` 계약을 지킨다.
탐지 결과를 `POST /internal/inference/events`로 보낸다([결정 0011]). fastapi는 같은
`event_id`를 멱등 처리하므로 재전송이 상태를 두 번 바꾸지 않는다.

**전송은 부수 효과다.** 탐지 루프는 이 핸들러가 죽어도 계속 돌아야 한다. 전송 실패는
로그로만 남기고 예외를 밖으로 새지 않게 만든다.

[결정 0011]: ../../docs/architecture/decisions.md
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime

import requests
from shared.types import CapturedFrame

from .consumer import ResultHandler, log_result
from .types import InferenceResult

logger = logging.getLogger(__name__)

INTERNAL_EVENTS_PATH = "/internal/inference/events"
# connect + read 통합 타임아웃. 빠진 채널이 전송 스레드를 오래 붙잡지 않게 한다.
POST_TIMEOUT_SECONDS = 5.0
# 초기 전송 실패 후 최대 재시도 횟수. backoff은 시도마다 1초, 2초씩 늘린다.
MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = (1.0, 2.0)

__all__ = ["FastAPIResultHandler", "build_event_id", "build_event_payload"]


def build_event_id(captured: CapturedFrame) -> str:
    """이벤트를 유일하게 식별하는 id를 만든다.

    규칙: `{camera_id}-{YYYYMMDDTHHMMSSsssZ}-{sequence}` (basic ISO 8601, 밀리초 3자리).

    같은 카메라·시각·프레임 번호면 항상 같은 id가 나와야 fastapi의 멱등 처리가
    성립한다. 재전송으로 같은 id가 두 번 들어와도 상태가 두 번 바뀌지 않는다.
    """
    captured_at = captured.captured_at.astimezone(UTC)
    timestamp = (
        captured_at.strftime("%Y%m%dT%H%M%S")
        + f"{captured_at.microsecond // 1000:03d}Z"
    )
    return f"{captured.camera_id}-{timestamp}-{captured.sequence}"


def build_event_payload(
    captured: CapturedFrame, result: InferenceResult
) -> dict[str, object]:
    """`/internal/inference/events` 스키마에 맞는 요청 본문을 만든다."""
    event_id = build_event_id(captured)
    detections: list[dict[str, object]] = []
    for index, detection in enumerate(result.detections):
        item: dict[str, object] = {
            "detection_id": f"{event_id}-det-{index}",
            "class_id": detection.class_id,
            "class_name": detection.class_name,
            "confidence": detection.confidence,
            "bbox": list(detection.bbox),
        }
        # 미식별 탐지는 선택 필드를 생략한다. 얼굴 식별 모델이 값을 만든 경우에만
        # 내부 API로 전달하며, 이름·학번·학생 상태는 FastAPI가 보강·판정한다.
        is_identified = (
            detection.student_id is not None and detection.identity_confidence is not None
        )
        if is_identified:
            item["student_id"] = detection.student_id
            item["identity_confidence"] = detection.identity_confidence
        if is_identified and detection.face_bbox is not None:
            item["face_bbox"] = list(detection.face_bbox)
        # 신원과 달리 track_id는 단독으로도 뜻이 있다. 신원이 없는 track도 같은 사람을
        # 이어 본 결과이므로 식별 여부와 무관하게 보낸다.
        if detection.track_id is not None:
            item["track_id"] = detection.track_id
        if not is_identified and any(
            value is not None
            for value in (
                detection.student_id,
                detection.identity_confidence,
                detection.face_bbox,
            )
        ):
            logger.warning(
                "불완전한 학생 식별 필드를 미식별로 전송합니다. detection_id=%s",
                item["detection_id"],
            )
        detections.append(item)
    return {
        "event_id": event_id,
        "camera_id": captured.camera_id,
        "captured_at": captured.captured_at.isoformat(),
        "sequence": captured.sequence,
        "frame": {
            "width_pixels": result.frame_shape[1],
            "height_pixels": result.frame_shape[0],
        },
        "detections": detections,
    }


class FastAPIResultHandler:
    """탐지 결과를 HTTP로 보낸다. `ResultHandler` 자리에 그대로 들어간다.

    재시도 정책은 "제한 재시도"다. 초기 1회에 재시도 `max_retries`회를 더하고,
    사이 대기는 지수 backoff이다. 모두 실패하면 로그만 남기고 다음 프레임으로
    넘어간다. **전송 실패가 추론 파이프라인을 멈추지 않는다.**
    """

    def __init__(
        self,
        fastapi_url: str,
        *,
        timeout_seconds: float = POST_TIMEOUT_SECONDS,
        max_retries: int = MAX_RETRIES,
        backoff_seconds: tuple[float, float] = RETRY_BACKOFF_SECONDS,
        post: Callable[..., requests.Response] = requests.post,
        sleep: Callable[[float], None] = time.sleep,
        inner: ResultHandler = log_result,
    ) -> None:
        # base URL 끝에 슬래시가 있어도 경로가 두 번 이어지지 않게 정리한다.
        self._events_url = fastapi_url.rstrip("/") + INTERNAL_EVENTS_PATH
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        # HTTP 호출과 대기를 주입할 수 있게 둔다. 테스트에서 실제 네트워크를
        # 쓰지 않도록 대역으로 바꿔 넣는다.
        self._post = post
        self._sleep = sleep
        self._inner = inner

    def __call__(self, captured: CapturedFrame, result: InferenceResult) -> None:
        # 기존 로그 동작을 먼저 그대로 수행한다. HTTP 전송만 켰다고 탐지 로그가
        # 사라지면, 전송이 실패하는 동안 무슨 일이 있었는지 알 수 없다.
        self._inner(captured, result)

        payload = build_event_payload(captured, result)
        event_id = payload["event_id"]
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                response = self._post(
                    self._events_url, json=payload, timeout=self._timeout_seconds
                )
                response.raise_for_status()
            except Exception as error:
                # requests는 네트워크·타임아웃·HTTP 상태를 `RequestException` 계열로
                # 낸다. 본문 직렬화 같은 뜻밖의 오류도 여기서 같은 경로를 탄다.
                # 소비자 루프는 핸들러 호출을 감싸지 않으므로, 여기서 예외를 새면
                # 파이프라인 전체가 죽는다. 그래서 예외 종류를 가리지 않고 잡는다.
                last_error = error
                if attempt < self._max_retries:
                    # backoff 목록이 재시도 횟수보다 짧으면 마지막 값을 반복한다.
                    # 인덱스 경계 예외가 여기서 새면 파이프라인 전체가 죽는다.
                    wait_seconds = self._backoff_seconds[
                        min(attempt, len(self._backoff_seconds) - 1)
                    ]
                    logger.warning(
                        "카메라 %s 프레임 %d 이벤트 %s 전송 실패, %.1f초 후 재시도 "
                        "(%d/%d): %s",
                        captured.camera_id,
                        captured.sequence,
                        event_id,
                        wait_seconds,
                        attempt + 1,
                        self._max_retries,
                        error,
                    )
                    self._sleep(wait_seconds)
                continue

            logger.info(
                "카메라 %s 프레임 %d 이벤트 %s 전송 성공 (HTTP %d)",
                captured.camera_id,
                captured.sequence,
                event_id,
                response.status_code,
            )
            return

        # 모두 실패해도 다음 프레임으로 진행한다. 실패 원인은 로그로 남는다.
        logger.error(
            "카메라 %s 프레임 %d 이벤트 %s 전송 실패 (%d회 모두): %s",
            captured.camera_id,
            captured.sequence,
            event_id,
            self._max_retries + 1,
            last_error,
        )
