"""샘플링된 프레임을 학습용 이미지로 저장한다.

**어느 프레임을 고를지는 여기서 정하지 않는다.** 샘플링 판단은 CameraPipeline이
한 번만 내리고 그 결과를 이 저장기와 추론 버퍼에 함께 전달한다. 저장기와 버퍼가
각자 세면 디스크에 남은 학습용 이미지와 추론에 들어간 프레임이 서로 달라져,
나중에 탐지 결과를 이미지로 되짚을 수 없다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import cv2
from shared.types import Frame

logger = logging.getLogger(__name__)

ImageWriter = Callable[[Path, Frame], bool]


def _write_image(path: Path, frame: Frame) -> bool:
    return cv2.imwrite(str(path), frame)


class FrameCapture:
    """넘겨받은 프레임을 카메라별·날짜별 디렉터리에 JPEG으로 저장한다."""

    def __init__(
        self,
        *,
        camera_id: str,
        output_dir: Path,
        image_writer: ImageWriter = _write_image,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._camera_id = camera_id
        self._output_dir = output_dir
        self._image_writer = image_writer
        self._now = now

    def save(self, frame: Frame) -> Path | None:
        """프레임을 저장하고 경로를 돌려준다. 저장에 실패하면 None."""
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
