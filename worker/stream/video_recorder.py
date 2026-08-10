"""원본 영상을 세그먼트 단위로 로컬에 저장한다.

개발 중 학습 데이터와 재현 자료를 확보하기 위한 임시 수단이다.
운영 보관 수단이 아니다. 저장 범위·보존 기간·접근 권한이 합의되면 저장 주체는
recorder worker로 옮기고 MinIO에 적재한다(결정 0004).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Protocol

import cv2

from .camera_reader import Frame

logger = logging.getLogger(__name__)


class VideoWriterLike(Protocol):
    """OpenCV VideoWriter 중 이 모듈이 쓰는 부분만 추린 것."""

    def write(self, image: Frame) -> None: ...

    def release(self) -> None: ...


WriterFactory = Callable[[Path, int, tuple[int, int]], VideoWriterLike]


def _open_writer(path: Path, fps: int, frame_size: tuple[int, int]) -> VideoWriterLike:
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer: VideoWriterLike = cv2.VideoWriter(str(path), fourcc, fps, frame_size)
    return writer


class VideoRecorder:
    """카메라 한 대의 영상을 일정 시간마다 새 파일로 나눠 저장한다."""

    def __init__(
        self,
        *,
        camera_id: str,
        output_dir: Path,
        fps: int,
        segment_seconds: int,
        writer_factory: WriterFactory = _open_writer,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._camera_id = camera_id
        self._output_dir = output_dir
        self._fps = fps
        self._segment_seconds = segment_seconds
        self._writer_factory = writer_factory
        self._now = now

        self._writer: VideoWriterLike | None = None
        self._frame_size: tuple[int, int] | None = None
        self._segment_started_at: datetime | None = None

    def write(self, frame: Frame) -> None:
        """프레임을 현재 세그먼트에 쓴다. 세그먼트 시간이 지나면 새 파일로 넘어간다."""
        height, width = frame.shape[:2]
        frame_size = (width, height)

        if self._writer is None:
            self._start_segment(frame_size)
        elif frame_size != self._frame_size:
            # VideoWriter는 생성 시 지정한 크기와 다른 프레임을 조용히 버린다.
            # 해상도가 바뀌면 새 세그먼트를 열어야 빈 파일이 생기지 않는다.
            logger.info(
                "카메라 %s 해상도가 %s에서 %s로 바뀌어 세그먼트를 새로 연다",
                self._camera_id,
                self._frame_size,
                frame_size,
            )
            self._start_segment(frame_size)
        elif self._elapsed_seconds() >= self._segment_seconds:
            self._start_segment(frame_size)

        assert self._writer is not None  # _start_segment가 항상 채운다
        self._writer.write(frame)

    def close(self) -> None:
        if self._writer is not None:
            self._writer.release()
            self._writer = None
            self._segment_started_at = None

    def _elapsed_seconds(self) -> float:
        if self._segment_started_at is None:
            return 0.0
        # timedelta.seconds는 일 단위를 버린다. 긴 세그먼트에서 어긋나므로
        # total_seconds()를 쓴다.
        return (self._now() - self._segment_started_at).total_seconds()

    def _start_segment(self, frame_size: tuple[int, int]) -> None:
        self.close()

        started_at = self._now()
        segment_dir = self._output_dir / self._camera_id / started_at.strftime("%Y-%m-%d")
        segment_dir.mkdir(parents=True, exist_ok=True)
        segment_path = segment_dir / f"{started_at.strftime('%Y%m%d_%H%M%S')}.mp4"

        # 프레임 크기는 설정이 아니라 실제 프레임에서 가져온다. 설정값과 실제 해상도가
        # 어긋나면 VideoWriter가 오류 없이 빈 파일을 만든다.
        self._writer = self._writer_factory(segment_path, self._fps, frame_size)
        self._frame_size = frame_size
        self._segment_started_at = started_at
        logger.info("카메라 %s 영상 저장 시작: %s", self._camera_id, segment_path)
