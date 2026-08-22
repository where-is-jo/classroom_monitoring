"""카메라 프레임 bbox 중심점을 좌석 ROI polygon에 매핑하는 순수 규칙."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from .models import Point, RoiConnection


class RoiMappingReason(StrEnum):
    """bbox가 좌석 하나로 매핑되지 않은 이유."""

    MATCHED = "MATCHED"
    INVALID_INPUT = "INVALID_INPUT"
    NO_MATCH = "NO_MATCH"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class RoiMappingResult:
    """좌석 ROI 매핑 결과와 안전한 진단 코드."""

    connection: RoiConnection | None
    reason: RoiMappingReason


def find_roi_connection_for_bbox(
    bbox: tuple[int, int, int, int],
    *,
    frame_width_pixels: int,
    frame_height_pixels: int,
    connections: Sequence[RoiConnection],
) -> RoiConnection | None:
    """bbox 중심이 정확히 한 polygon에 있을 때만 해당 ROI를 반환한다."""
    return map_bbox_to_roi(
        bbox,
        frame_width_pixels=frame_width_pixels,
        frame_height_pixels=frame_height_pixels,
        connections=connections,
    ).connection


def map_bbox_to_roi(
    bbox: tuple[int, int, int, int],
    *,
    frame_width_pixels: int,
    frame_height_pixels: int,
    connections: Sequence[RoiConnection],
) -> RoiMappingResult:
    """bbox 중심을 매핑하고 미매핑 원인을 분리한다."""
    if frame_width_pixels <= 0 or frame_height_pixels <= 0:
        return RoiMappingResult(None, RoiMappingReason.INVALID_INPUT)
    x_min, y_min, x_max, y_max = bbox
    if x_max <= x_min or y_max <= y_min:
        return RoiMappingResult(None, RoiMappingReason.INVALID_INPUT)
    # 탐지기는 프레임 경계를 넘는 bbox를 낸다. 화면 끝에 선 사람의 상자가 몇 px
    # 삐져나왔다는 이유로 탐지를 통째로 버리면, 가장자리 좌석이 조용히 관측에서
    # 빠진다. 프레임 안으로 잘라낸 뒤 그 상자의 중심으로 판정한다.
    x_min, x_max = _clamp(x_min, frame_width_pixels), _clamp(x_max, frame_width_pixels)
    y_min, y_max = _clamp(y_min, frame_height_pixels), _clamp(y_max, frame_height_pixels)
    if x_max <= x_min or y_max <= y_min:
        # 잘라내고 나니 면적이 없다 = bbox 전체가 프레임 밖이다.
        return RoiMappingResult(None, RoiMappingReason.INVALID_INPUT)
    center = Point(
        x=(x_min + x_max) / 2 / frame_width_pixels,
        y=(y_min + y_max) / 2 / frame_height_pixels,
    )
    matches = [
        connection for connection in connections if _point_in_polygon(center, connection.polygon)
    ]
    if len(matches) == 1:
        return RoiMappingResult(matches[0], RoiMappingReason.MATCHED)
    if len(matches) > 1:
        return RoiMappingResult(None, RoiMappingReason.AMBIGUOUS)
    return RoiMappingResult(None, RoiMappingReason.NO_MATCH)


def _clamp(value: int, maximum: int) -> int:
    """좌표를 `[0, maximum]` 안으로 잘라낸다."""
    return min(max(value, 0), maximum)


def _point_in_polygon(point: Point, polygon: tuple[Point, ...]) -> bool:
    """경계를 포함하는 ray-casting 판정."""
    if len(polygon) < 3:
        return False
    inside = False
    previous = polygon[-1]
    for current in polygon:
        if _point_on_segment(point, previous, current):
            return True
        crosses = (current.y > point.y) != (previous.y > point.y)
        if crosses:
            x_at_y = (previous.x - current.x) * (point.y - current.y) / (
                previous.y - current.y
            ) + current.x
            if point.x < x_at_y:
                inside = not inside
        previous = current
    return inside


def _point_on_segment(point: Point, start: Point, end: Point) -> bool:
    cross = (point.y - start.y) * (end.x - start.x) - (point.x - start.x) * (end.y - start.y)
    epsilon = 1e-12
    if abs(cross) > epsilon:
        return False
    return (
        min(start.x, end.x) - epsilon <= point.x <= max(start.x, end.x) + epsilon
        and min(start.y, end.y) - epsilon <= point.y <= max(start.y, end.y) + epsilon
    )
