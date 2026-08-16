"""탐지 결과를 카메라별 ROI에 대조해 좌석 점유 관측으로 바꾸는 순수 규칙.

좌석 위치의 정본은 `roi_connections.polygon` 하나다(결정 0020). `seat.geometry`는
배치도를 그리는 좌표라 카메라 화각과 무관하며 판정에 쓰지 않는다.

**관측 대상은 그 카메라에 ROI가 등록된 좌석뿐이다.** 카메라가 강의실의 일부만
보는 분할 관측에서, 보지도 못한 좌석을 "비어 있음"으로 기록하지 않기 위해서다.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..classrooms.models import SeatObservation
from ..roi_connections.mapping import map_bbox_to_roi
from ..roi_connections.models import RoiConnection
from .models import Detection, FrameInfo


def map_detections_to_observations(
    detections: Sequence[Detection],
    connections: Sequence[RoiConnection],
    frame: FrameInfo,
    confidence_threshold: float,
) -> tuple[SeatObservation, ...]:
    """탐지 결과를 좌석별 점유 관측으로 변환한다.

    - `confidence_threshold` 미만 탐지는 점유 증거로 쓰지 않는다.
    - bbox 중심이 정확히 하나의 ROI에 들어갈 때만 그 좌석을 점유로 본다.
      겹치는 ROI(AMBIGUOUS)와 어디에도 없는 bbox(NO_MATCH)는 증거가 되지 않는다.
    - 여러 사람이 같은 좌석에 겹치면 신뢰도가 높은 사람만 채택한다.
    - 반환 대상은 `connections`에 있는 좌석뿐이다. 매칭되지 않은 좌석은
      "관측했으나 비어 있음"으로 남는다 — 그 카메라가 실제로 보는 자리이기 때문이다.
    """
    best_by_seat: dict[str, Detection] = {}
    for detection in detections:
        if detection.confidence < confidence_threshold:
            continue
        matched = map_bbox_to_roi(
            detection.bbox,
            frame_width_pixels=frame.width_pixels,
            frame_height_pixels=frame.height_pixels,
            connections=connections,
        ).connection
        if matched is None:
            continue
        current = best_by_seat.get(matched.seat_id)
        if current is None or detection.confidence > current.confidence:
            best_by_seat[matched.seat_id] = detection

    observations: list[SeatObservation] = []
    seen: set[str] = set()
    for connection in connections:
        # 같은 좌석에 ROI가 여러 번 등록돼도 관측은 좌석당 하나만 만든다.
        if connection.seat_id in seen:
            continue
        seen.add(connection.seat_id)
        matched_detection = best_by_seat.get(connection.seat_id)
        if matched_detection is None:
            observations.append(
                SeatObservation(seat_id=connection.seat_id, occupied=False, confidence=0.0)
            )
        else:
            observations.append(
                SeatObservation(
                    seat_id=connection.seat_id,
                    occupied=True,
                    confidence=matched_detection.confidence,
                )
            )
    return tuple(observations)
