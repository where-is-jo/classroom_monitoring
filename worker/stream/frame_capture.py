"""샘플링한 프레임을 학습용 이미지로 저장한다.

모든 프레임을 다루지 않는다. 샘플링 주기는 설정값이며, 이 주기가 나중에
deeplearning으로 보낼 프레임을 고르는 기준과 같은 값이다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import cv2

from .camera_reader import Frame

logger = logging.getLogger(__name__)

ImageWriter = Callable[[Path, Frame], bool]


def _write_image(path: Path, frame: Frame) -> bool:
    return cv2.imwrite(str(path), frame)


def should_sample(frame_index: int, interval_frames: int) -> bool:
    """이 프레임을 샘플링 대상으로 고를지 판단한다.

    프레임 번호만 보는 순수 함수라 실제 카메라 없이 검증할 수 있다.
    """
    if interval_frames < 1:
        raise ValueError("샘플링 주기는 1 이상이어야 합니다.")
    return frame_index % interval_frames == 0


class FrameCapture:
    """샘플링된 프레임을 카메라별·날짜별 디렉터리에 JPEG으로 저장한다."""

    def __init__(
        self,
        *,
        camera_id: str,
        output_dir: Path,
        interval_frames: int,
        image_writer: ImageWriter = _write_image,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._camera_id = camera_id
        self._output_dir = output_dir
        self._interval_frames = interval_frames
        self._image_writer = image_writer
        self._now = now
        self._frame_index = 0

    def offer(self, frame: Frame) -> Path | None:
        """프레임을 넘긴다. 샘플링 대상이면 저장하고 경로를, 아니면 None을 반환한다."""
        is_sampled = should_sample(self._frame_index, self._interval_frames)
        self._frame_index += 1

        if not is_sampled:
            return None

        captured_at = self._now()
        capture_dir = self._output_dir / self._camera_id / captured_at.strftime("%Y-%m-%d")
        capture_dir.mkdir(parents=True, exist_ok=True)
        # 같은 초에 여러 장이 나올 수 있어 마이크로초까지 넣는다.
        capture_path = capture_dir / f"{captured_at.strftime('%Y%m%d_%H%M%S_%f')}.jpg"

        if not self._image_writer(capture_path, frame):
            # 디스크가 찼거나 경로에 쓸 수 없는 경우다. 조용히 넘기면 학습 데이터가
            # 비어 있는 것을 나중에야 알게 된다.
            logger.error("카메라 %s 프레임 저장 실패: %s", self._camera_id, capture_path)
            return None

        return capture_path
