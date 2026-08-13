"""탐지 결과를 좌석 점유 관측으로 변환하는 매핑 로직.

bbox의 중심점을 프레임 크기로 정규화한 뒤 좌석 geometry(0~1 영역)와 대조해
한 사람이 어느 좌석에 앉았는지 판정한다. 여러 사람이 한 좌석에 겹치면
신뢰도가 가장 높은 사람만 채택한다.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..student_monitoring.models import Detection, FrameInfo
from .models import Seat, SeatObservation


def bbox_center(bbox: tuple[int, int, int, int]) -> tuple[float, float]:
    """bbox의 중심점을 픽셀 좌표로 돌려준다."""
    x_min, y_min, x_max, y_max = bbox
    return ((x_min + x_max) / 2, (y_min + y_max) / 2)


def find_seat_for_detection(
    detection: Detection,
    seats: Sequence[Seat],
    frame_width: int,
    frame_height: int,
) -> Seat | None:
    """한 사람의 bbox 중심점이 속한 좌석을 돌려준다. 없으면 None.

    - geometry가 없는 좌석은 매핑 후보에서 제외한다.
    - 사람이 여러 좌석에 걸치더라도 중심점이 속한 좌석 하나만 고른다.
    """
    cx, cy = bbox_center(detection.bbox)
    norm_x = cx / frame_width
    norm_y = cy / frame_height

    for seat in seats:
        if seat.geometry is None:
            continue
        gx = seat.geometry.x
        gy = seat.geometry.y
        gw = seat.geometry.width
        gh = seat.geometry.height
        if gx <= norm_x <= gx + gw and gy <= norm_y <= gy + gh:
            return seat
    return None


def map_detections_to_observations(
    detections: Sequence[Detection],
    seats: Sequence[Seat],
    frame: FrameInfo,
    confidence_threshold: float,
) -> tuple[SeatObservation, ...]:
    """탐지 결과를 좌석별 점유 관측으로 변환한다.

    - `confidence_threshold` 미만 탐지는 점유 증거로 쓰지 않는다.
    - bbox가 모든 좌석 밖이면 어떤 좌석도 점유하지 않는다.
    - 탐지가 없거나 좌석에 매핑된 탐지가 없으면 UNKNOWN(occupied=False, confidence=0.0)으로 둔다.
    - 여러 사람이 같은 좌석에 겹치면 신뢰도가 높은 사람만 채택한다.
    - geometry가 없는 좌석은 매핑에서 제외한다.
    """
    best_by_seat: dict[str, Detection] = {}
    for detection in detections:
        if detection.confidence < confidence_threshold:
            continue
        seat = find_seat_for_detection(
            detection,
            seats,
            frame.width_pixels,
            frame.height_pixels,
        )
        if seat is None:
            continue
        current = best_by_seat.get(seat.id)
        if current is None or detection.confidence > current.confidence:
            best_by_seat[seat.id] = detection

    observations: list[SeatObservation] = []
    for seat in seats:
        if seat.geometry is None:
            continue
        matched = best_by_seat.get(seat.id)
        if matched is None:
            observations.append(SeatObservation(seat_id=seat.id, occupied=False, confidence=0.0))
        else:
            observations.append(
                SeatObservation(
                    seat_id=seat.id,
                    occupied=True,
                    confidence=matched.confidence,
                )
            )
    return tuple(observations)
