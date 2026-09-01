"""탐지 이벤트 저장소에서 사람 bbox 중심을 읽어 오는 어댑터.

탐지 이벤트는 `student_monitoring`이 소유한다. ROI 서비스는 그 저장소의 페이지 조회
방식을 알 필요가 없으므로, 여기서 페이지를 넘겨 가며 읽고 정규화 좌표로 바꿔 준다.
"""

from __future__ import annotations

import logging
from datetime import datetime

from ...student_monitoring.ports import DetectionEventRepository
from ..detection_layout import DetectionSample

logger = logging.getLogger(__name__)

# 한 번에 읽을 이벤트 수. 너무 크면 응답이 통째로 메모리에 올라오고, 너무 작으면
# 왕복이 늘어난다.
_PAGE_SIZE = 500


class DetectionEventSeatedSource:
    """탐지 이벤트에서 사람 탐지의 bbox 중심만 뽑아 준다."""

    def __init__(self, repository: DetectionEventRepository, *, max_events: int) -> None:
        self._repository = repository
        # 조회 상한. 기간을 길게 잡아도 응답 시간과 메모리가 예측 가능한 범위에 있게 한다.
        self._max_events = max_events

    def list_recent_centers(
        self,
        camera_id: str,
        *,
        since: datetime,
        until: datetime,
    ) -> list[DetectionSample]:
        samples: list[DetectionSample] = []
        cursor: str | None = None
        read = 0
        while read < self._max_events:
            page = self._repository.find_by_camera_and_period(
                camera_id,
                since,
                until,
                limit=min(_PAGE_SIZE, self._max_events - read),
                cursor=cursor,
            )
            if not page.items:
                break
            read += len(page.items)
            for event in page.items:
                width = event.frame.width_pixels
                height = event.frame.height_pixels
                if width <= 0 or height <= 0:
                    # 프레임 크기를 모르면 정규화할 수 없다. 추정하지 않고 건너뛴다.
                    continue
                for detection in event.detections:
                    x_min, y_min, x_max, y_max = detection.bbox
                    if x_max <= x_min or y_max <= y_min:
                        continue
                    samples.append(
                        DetectionSample(
                            x=(x_min + x_max) / 2 / width,
                            y=(y_min + y_max) / 2 / height,
                            track_id=detection.track_id,
                            captured_at=event.captured_at,
                        )
                    )
            cursor = page.next_cursor
            if cursor is None:
                break
        logger.info(
            "카메라 %s의 탐지 표본 %d개를 읽었다 (이벤트 %d건)", camera_id, len(samples), read
        )
        return samples
