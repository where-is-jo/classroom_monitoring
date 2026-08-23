"""탐지 시점 스냅샷을 객체 저장소에 올린다.

영상 원본을 저장하지 않고 정지 이미지만 남긴다([결정 0011]). 프레임을 이미 들고 있는
쪽이 inference라 여기서 만든다. recorder처럼 RTSP를 따로 받지 않는다.

**업무 의미를 붙이지 않는다.** 여기서 보는 것은 "탐지 개수가 직전 적재와 다른가"까지다.
"학생이 자리를 비웠다" 같은 해석은 fastapi가 한다(결정 0008·0009).

[결정 0011]: ../../docs/architecture/decisions.md
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import cv2
import numpy as np
from shared.object_keys import build_object_key
from shared.object_storage import ObjectStorage, ObjectStorageError
from shared.types import CapturedFrame, Frame

from .consumer import ResultHandler, log_result
from .types import InferenceResult

logger = logging.getLogger(__name__)

SNAPSHOT_CONTENT_TYPE = "image/jpeg"
SNAPSHOT_SUFFIX = ".jpg"

__all__ = [
    "SNAPSHOT_CONTENT_TYPE",
    "SnapshotEncodeError",
    "SnapshotResultHandler",
    "encode_snapshot",
]


class SnapshotEncodeError(Exception):
    """프레임을 JPEG로 인코딩하지 못했다."""


def encode_snapshot(frame: Frame, *, max_long_side_px: int, jpeg_quality: int) -> bytes:
    """프레임을 JPEG 바이트로 만든다. 긴 변이 상한을 넘을 때만 줄인다.

    **확대하지 않는다.** 원본보다 키우면 용량만 늘고 담긴 정보는 그대로다.
    축소에는 INTER_AREA를 쓴다. 축소에서 가장 덜 깨지는 보간이다.
    """
    height, width = frame.shape[:2]
    long_side = max(height, width)
    if long_side > max_long_side_px:
        scale = max_long_side_px / long_side
        frame = cv2.resize(
            frame,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )

    is_encoded, buffer = cv2.imencode(
        SNAPSHOT_SUFFIX, frame, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality]
    )
    if not is_encoded:
        raise SnapshotEncodeError("프레임을 JPEG로 인코딩하지 못했습니다.")
    return bytes(np.asarray(buffer).tobytes())


class SnapshotResultHandler:
    """탐지 개수가 바뀌면 스냅샷을 올린다. `ResultHandler` 자리에 그대로 들어간다.

    상태는 카메라별 dict 두 개뿐이다. **소비자 스레드가 하나라는 전제로 락을 두지
    않았다.** `InferenceConsumer`를 여러 스레드로 돌리게 되면 여기가 깨진다.
    """

    def __init__(
        self,
        *,
        storage: ObjectStorage,
        min_interval_seconds: float,
        max_long_side_px: int,
        jpeg_quality: int,
        inner: ResultHandler = log_result,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._storage = storage
        self._min_interval_seconds = min_interval_seconds
        self._max_long_side_px = max_long_side_px
        self._jpeg_quality = jpeg_quality
        self._inner = inner
        # 시스템 시각이 바뀌어도 간격 판정이 흔들리지 않도록 단조 시계를 쓴다.
        self._monotonic = monotonic

        self._last_uploaded_count: dict[str, int] = {}
        # "올린" 시각이 아니라 "시도한" 시각이다. 실패도 시간을 쓰기 때문에
        # 성공만 기록하면 저장소가 죽어 있는 동안 간격 제한이 걸리지 않는다.
        self._last_attempt_at: dict[str, float] = {}

    def __call__(self, captured: CapturedFrame, result: InferenceResult) -> None:
        # 기존 로그 동작을 먼저 그대로 수행한다. 스냅샷을 켰다고 로그가 사라지면
        # 적재가 실패하는 동안 무슨 일이 있었는지 알 수 없다.
        self._inner(captured, result)

        camera_id = captured.camera_id
        count = len(result.detections)

        # 직전에 "올린" 개수와 비교한다. 마지막으로 "본" 개수가 아니다.
        # 간격 캡에 막혀 건너뛴 변화가 다음 기회에 그대로 올라가야 한다.
        # 아직 아무것도 올리지 않았으면 0을 기준으로 본다. 그래야 기동 직후
        # 아무도 없는 화면을 한 장 올리는 일이 없다.
        if self._last_uploaded_count.get(camera_id, 0) == count:
            return

        now = self._monotonic()
        last_at = self._last_attempt_at.get(camera_id)
        if last_at is not None and now - last_at < self._min_interval_seconds:
            # 탐지가 경계에서 떨릴 때(occupied ↔ vacant 반복) 적재가 폭주하는 것을
            # 막는 유일한 장치다. 용량 계산이 이 상한에 기대고 있다.
            logger.debug(
                "카메라 %s 스냅샷을 건너뛴다 (최소 간격 %.1f초 안)",
                camera_id,
                self._min_interval_seconds,
            )
            return

        # **시도 시각은 결과와 무관하게 먼저 남긴다.** 성공했을 때만 남기면,
        # 저장소가 한 번도 성공하지 못한 동안 last_at이 계속 비어 있어 간격 제한이
        # 걸리지 않는다. MinIO가 내려가 있으면 개수가 바뀐 프레임마다 접속
        # timeout(5초)을 그대로 기다리게 되고, 그 시간만큼 추론 소비자 스레드가
        # 멈춰 프레임이 버려진다. 저장소 장애가 탐지를 갉아먹는 경로다.
        self._last_attempt_at[camera_id] = now

        key = build_object_key(camera_id, captured.captured_at, suffix=SNAPSHOT_SUFFIX)
        try:
            data = encode_snapshot(
                captured.frame,
                max_long_side_px=self._max_long_side_px,
                jpeg_quality=self._jpeg_quality,
            )
            stored = self._storage.put_bytes(
                key, data, content_type=SNAPSHOT_CONTENT_TYPE
            )
        except (ObjectStorageError, SnapshotEncodeError) as error:
            # 저장소 장애가 파이프라인을 멈추면 안 된다. 탐지는 계속 돌아야 한다.
            # 개수는 갱신하지 않으므로, 놓친 변화는 다음 간격이 지나면 그대로
            # 다시 올라간다. 저장소가 살아나면 저절로 이어진다.
            logger.warning("카메라 %s 스냅샷을 올리지 못했다: %s", camera_id, error)
            return

        # 개수는 성공했을 때만 갱신한다. 실패를 성공으로 치면 그 변화가 영영 사라진다.
        self._last_uploaded_count[camera_id] = count
        logger.info(
            "카메라 %s 스냅샷 적재: %s (%d bytes, 탐지 %d건)",
            camera_id,
            stored.key,
            stored.size_bytes,
            count,
        )
