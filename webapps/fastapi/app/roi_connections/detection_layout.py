"""실제 탐지가 몰린 자리에서 좌석 ROI 후보를 찾는 순수 규칙.

좌석 격자를 사영하는 방식([결정 0039](../../../docs/architecture/decisions.md))은 격자가
실제 배치와 어긋나면 그대로 어긋난다. 이 모듈은 반대로 간다 — **카메라가 이미 본 것**에서
자리를 찾는다. 사람이 오래 앉아 있던 곳에는 bbox 중심이 반복해서 찍히므로, 그 밀도의
봉우리가 곧 좌석이다.

세 가지를 지킨다.

1. **판정과 같은 점을 쓴다.** 좌석 판정은 bbox 중심이 ROI 안에 있는지로 정해지므로
   (`mapping.map_bbox_to_roi`), 여기서도 bbox 중심만 모은다. 다른 기준점으로 만든 ROI는
   판정에서 빗나간다.
2. **지나가는 사람을 세지 않는다.** 같은 track이 짧은 시간 안에 거의 움직이지 않았을 때만
   "앉아 있었다"로 본다. 통로에서 멈춘 사람이 좌석으로 굳어지는 것을 막는다.
3. **만든 ROI끼리 겹치지 않는다.** 겹치는 ROI에 들어간 bbox는 `AMBIGUOUS`가 되어 좌석을
   정하지 못한다([결정 0020](../../../docs/architecture/decisions.md)의 4번). 겹치면
   줄이고, 그래도 겹치면 만들지 않는다.

폴리곤 모양은 다듬지 않는다. 밀도가 그린 모양 그대로가 "그 자리에서 실제로 관측되는
영역"이며, 보기 좋은 사각형으로 바꾸면 관측되지 않는 영역이 섞여 들어간다.

이 모듈은 순수 계산만 한다 — 저장소도 FastAPI도 알지 못한다.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from .errors import RoiConnectionInputError
from .models import Point

# 밀도를 세는 격자 크기. 실제 CCTV(1280x1944)에서 한 칸이 20x20px쯤 되고, 앉은 사람의
# bbox 중심이 흔들리는 폭보다 작다. 이보다 잘게 나누면 한 자리가 여러 봉우리로 갈라지고,
# 굵게 나누면 옆자리와 붙는다.
HISTOGRAM_COLUMNS = 64
HISTOGRAM_ROWS = 96

# 봉우리에서 이 비율 위쪽까지를 한 군집으로 본다. 낮추면 영역이 넓어져 옆자리와 붙고,
# 높이면 실제로 관측된 영역을 다 담지 못해 판정에서 빠지는 프레임이 생긴다.
PEAK_RATIO = 0.28

# 군집 하나가 봉우리에서 뻗어 나갈 수 있는 최대 거리(칸). 한 자리가 옆자리까지
# 번지는 것을 막는 상한이다.
MAX_RADIUS_BINS = 4

# 중심이 이보다 가까운 군집은 같은 자리가 갈라진 것으로 보고 합친다.
MERGE_DISTANCE_BINS = 3

# 가장 강한 군집 대비 이 비율에 못 미치면 자리로 인정하지 않는다. 잠깐 서 있던 흔적이
# 좌석이 되는 것을 막는다.
MIN_SUPPORT_RATIO = 0.03

# 위 비율과 별개로 요구하는 최소 표본 수. 데이터가 적을 때 비율만으로는 걸러지지 않는다.
MIN_SUPPORT_SAMPLES = 40.0

# "앉아 있었다"의 기준. 이 시간 안에서 중심이 이 거리(정규화 좌표) 밖으로 움직이지
# 않았으면 정지로 본다. 실측 CCTV에서 0.02는 가로 26px·세로 39px쯤이다.
STATIONARY_WINDOW_SECONDS = 20.0
STATIONARY_RADIUS = 0.02

# 겹쳤을 때 줄이는 비율과 시도 횟수. 이만큼 줄여도 겹치면 그 군집은 버린다.
OVERLAP_SHRINK_FACTOR = 0.85
OVERLAP_SHRINK_ATTEMPTS = 4

# 겹침을 피해 줄이고 난 뒤에도 이만큼의 탐지를 담고 있어야 자리로 인정한다.
# **실제 데이터에서 드러난 결함을 막는 값이다.** 줄이기를 반복하면 겹침은 피하지만
# 관측된 영역 밖으로 쪼그라들어, 표본 하나짜리 껍데기가 좌석 ROI로 남을 수 있었다.
MIN_POLYGON_SAMPLES = 40


@dataclass(frozen=True)
class DetectionSample:
    """탐지 하나의 bbox 중심. 좌표는 0~1로 정규화한 값이다."""

    x: float
    y: float
    track_id: str | None
    captured_at: datetime


@dataclass(frozen=True)
class DetectionRoiCluster:
    """탐지가 몰린 자리 하나."""

    polygon: tuple[Point, ...]
    sample_count: int
    """이 폴리곤 안에 들어간 정지 탐지 수. 관리자가 신뢰도를 가늠하는 값이다."""

    support: float
    """평활화한 밀도 합. 정렬에만 쓰고 화면에는 보여주지 않는다."""


@dataclass(frozen=True)
class DetectionRoiPlan:
    clusters: tuple[DetectionRoiCluster, ...]
    sample_count: int
    """창 안에서 읽은 전체 탐지 수."""

    stationary_count: int
    """그중 정지로 판정한 수. 둘의 차이가 크면 사람이 많이 오갔다는 뜻이다."""

    dropped_overlapping: int
    """겹쳐서 만들지 않은 군집 수."""

    dropped_weak: int
    """표본이 적어 만들지 않은 군집 수."""


def plan_detection_rois(
    samples: Sequence[DetectionSample],
    *,
    max_clusters: int,
) -> DetectionRoiPlan:
    """탐지 표본에서 좌석 ROI 후보를 찾는다.

    `max_clusters`는 만들 수 있는 자리 수의 상한이다. 보통 강의실의 좌석 수를 넣는다.
    """
    if max_clusters < 1:
        raise RoiConnectionInputError("만들 ROI 수는 1개 이상이어야 합니다.")
    stationary = _stationary_samples(samples)
    if not stationary:
        return DetectionRoiPlan(
            clusters=(),
            sample_count=len(samples),
            stationary_count=0,
            dropped_overlapping=0,
            dropped_weak=0,
        )

    grid = _smooth(_histogram(stationary), times=2)
    raw = _grow_clusters(grid, max_clusters=max_clusters * 3)
    merged = _merge_close(raw)

    strongest = max((cluster.support for cluster in merged), default=0.0)
    minimum = max(strongest * MIN_SUPPORT_RATIO, MIN_SUPPORT_SAMPLES)

    accepted: list[DetectionRoiCluster] = []
    dropped_overlapping = 0
    dropped_weak = 0
    for cluster in sorted(merged, key=lambda item: -item.support):
        if len(accepted) >= max_clusters:
            break
        if cluster.support < minimum:
            dropped_weak += 1
            continue
        polygon = _hull(cluster.bins)
        if len(polygon) < 3:
            dropped_weak += 1
            continue
        placed = _resolve_overlap(polygon, accepted)
        if placed is None:
            dropped_overlapping += 1
            continue
        sample_count = _count_inside(stationary, placed)
        if sample_count < MIN_POLYGON_SAMPLES:
            # 겹침을 피하려고 줄이다 관측된 영역 밖으로 쪼그라든 경우다. 빈 ROI를
            # 좌석에 붙이면 그 좌석은 영영 비어 있는 것으로 기록된다.
            dropped_overlapping += 1
            continue
        accepted.append(
            DetectionRoiCluster(
                polygon=placed,
                sample_count=sample_count,
                support=cluster.support,
            )
        )

    # 화면에서 위에서 아래로 훑어 내려가며 좌석을 짚을 수 있게 정렬한다.
    ordered = tuple(sorted(accepted, key=lambda item: _reading_order(item.polygon)))
    return DetectionRoiPlan(
        clusters=ordered,
        sample_count=len(samples),
        stationary_count=len(stationary),
        dropped_overlapping=dropped_overlapping,
        dropped_weak=dropped_weak,
    )


@dataclass(frozen=True)
class _RawCluster:
    bins: tuple[tuple[int, int], ...]
    support: float


def _stationary_samples(samples: Sequence[DetectionSample]) -> list[tuple[float, float]]:
    """짧은 시간 안에서 거의 움직이지 않은 탐지만 남긴다.

    **track 전체가 아니라 탐지 하나하나를 본다.** 앉아 있다가 일어난 사람의 track에서
    앉아 있던 구간은 살려야 하기 때문이다. track이 없는 탐지는 움직였는지 알 수 없어
    쓰지 않는다 — 모르는 것을 관측으로 바꾸지 않는다.
    """
    by_track: dict[str, list[tuple[float, float, float]]] = {}
    for sample in samples:
        if sample.track_id is None:
            continue
        by_track.setdefault(sample.track_id, []).append(
            (sample.captured_at.timestamp(), sample.x, sample.y)
        )

    kept: list[tuple[float, float]] = []
    for points in by_track.values():
        points.sort()
        for index, (stamp, x, y) in enumerate(points):
            if _max_move_within_window(points, index, stamp, x, y) <= STATIONARY_RADIUS:
                kept.append((x, y))
    return kept


def _max_move_within_window(
    points: list[tuple[float, float, float]],
    index: int,
    stamp: float,
    x: float,
    y: float,
) -> float:
    moved = 0.0
    for other in range(index - 1, -1, -1):
        other_stamp, other_x, other_y = points[other]
        if stamp - other_stamp > STATIONARY_WINDOW_SECONDS:
            break
        moved = max(moved, math.hypot(x - other_x, y - other_y))
        if moved > STATIONARY_RADIUS:
            return moved
    for other in range(index + 1, len(points)):
        other_stamp, other_x, other_y = points[other]
        if other_stamp - stamp > STATIONARY_WINDOW_SECONDS:
            break
        moved = max(moved, math.hypot(x - other_x, y - other_y))
        if moved > STATIONARY_RADIUS:
            return moved
    return moved


def _histogram(centers: Sequence[tuple[float, float]]) -> list[list[float]]:
    grid = [[0.0] * HISTOGRAM_COLUMNS for _ in range(HISTOGRAM_ROWS)]
    for x, y in centers:
        column = min(max(int(x * HISTOGRAM_COLUMNS), 0), HISTOGRAM_COLUMNS - 1)
        row = min(max(int(y * HISTOGRAM_ROWS), 0), HISTOGRAM_ROWS - 1)
        grid[row][column] += 1
    return grid


def _smooth(grid: list[list[float]], *, times: int) -> list[list[float]]:
    """3x3 평균으로 다듬는다. 한 자리의 밀도가 인접 칸으로 갈라지는 것을 줄인다."""
    for _ in range(times):
        output = [[0.0] * HISTOGRAM_COLUMNS for _ in range(HISTOGRAM_ROWS)]
        for row in range(HISTOGRAM_ROWS):
            for column in range(HISTOGRAM_COLUMNS):
                total = 0.0
                count = 0
                for row_offset in (-1, 0, 1):
                    for column_offset in (-1, 0, 1):
                        near_row = row + row_offset
                        near_column = column + column_offset
                        if 0 <= near_row < HISTOGRAM_ROWS and 0 <= near_column < HISTOGRAM_COLUMNS:
                            total += grid[near_row][near_column]
                            count += 1
                output[row][column] = total / count
        grid = output
    return grid


def _grow_clusters(grid: list[list[float]], *, max_clusters: int) -> list[_RawCluster]:
    """가장 높은 봉우리부터 주변으로 번져 나가며 군집을 하나씩 떼어 낸다."""
    working = [row[:] for row in grid]
    clusters: list[_RawCluster] = []
    for _ in range(max_clusters):
        peak = 0.0
        seed: tuple[int, int] | None = None
        for row in range(HISTOGRAM_ROWS):
            for column in range(HISTOGRAM_COLUMNS):
                if working[row][column] > peak:
                    peak = working[row][column]
                    seed = (row, column)
        if seed is None or peak <= 0:
            break
        bins, support = _flood(working, seed, threshold=peak * PEAK_RATIO)
        if not bins:
            break
        clusters.append(_RawCluster(bins=tuple(bins), support=support))
    return clusters


def _flood(
    working: list[list[float]], seed: tuple[int, int], *, threshold: float
) -> tuple[list[tuple[int, int]], float]:
    stack = [seed]
    seen = {seed}
    bins: list[tuple[int, int]] = []
    support = 0.0
    while stack:
        row, column = stack.pop()
        if working[row][column] < threshold:
            continue
        if abs(row - seed[0]) > MAX_RADIUS_BINS or abs(column - seed[1]) > MAX_RADIUS_BINS:
            continue
        bins.append((row, column))
        support += working[row][column]
        working[row][column] = 0.0
        for row_offset, column_offset in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            near_row = row + row_offset
            near_column = column + column_offset
            if (
                0 <= near_row < HISTOGRAM_ROWS
                and 0 <= near_column < HISTOGRAM_COLUMNS
                and (near_row, near_column) not in seen
            ):
                seen.add((near_row, near_column))
                stack.append((near_row, near_column))
    return bins, support


def _merge_close(clusters: Sequence[_RawCluster]) -> list[_RawCluster]:
    """중심이 가까운 군집을 합친다. 한 자리가 두 봉우리로 갈라진 경우다."""
    merged: list[list[tuple[int, int]]] = []
    supports: list[float] = []
    for cluster in sorted(clusters, key=lambda item: -item.support):
        row, column = _bin_centroid(cluster.bins)
        target = None
        for index, bins in enumerate(merged):
            other_row, other_column = _bin_centroid(tuple(bins))
            if (
                abs(row - other_row) <= MERGE_DISTANCE_BINS
                and abs(column - other_column) <= MERGE_DISTANCE_BINS
            ):
                target = index
                break
        if target is None:
            merged.append(list(cluster.bins))
            supports.append(cluster.support)
        else:
            merged[target].extend(cluster.bins)
            supports[target] += cluster.support
    return [
        _RawCluster(bins=tuple(bins), support=support)
        for bins, support in zip(merged, supports, strict=True)
    ]


def _bin_centroid(bins: tuple[tuple[int, int], ...]) -> tuple[float, float]:
    return (
        sum(row for row, _ in bins) / len(bins),
        sum(column for _, column in bins) / len(bins),
    )


def _hull(bins: tuple[tuple[int, int], ...]) -> tuple[Point, ...]:
    """군집이 차지한 칸들의 볼록 껍질. 볼록이라 자기 교차가 생기지 않는다."""
    corners: list[tuple[float, float]] = []
    for row, column in bins:
        for row_offset in (0, 1):
            for column_offset in (0, 1):
                corners.append(
                    (
                        (column + column_offset) / HISTOGRAM_COLUMNS,
                        (row + row_offset) / HISTOGRAM_ROWS,
                    )
                )
    return tuple(Point(x=x, y=y) for x, y in _convex_hull(corners))


def _convex_hull(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Andrew's monotone chain."""
    unique = sorted(set(points))
    if len(unique) <= 2:
        return unique

    def cross(
        origin: tuple[float, float], first: tuple[float, float], second: tuple[float, float]
    ) -> float:
        return (first[0] - origin[0]) * (second[1] - origin[1]) - (first[1] - origin[1]) * (
            second[0] - origin[0]
        )

    lower: list[tuple[float, float]] = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def _resolve_overlap(
    polygon: tuple[Point, ...], accepted: Sequence[DetectionRoiCluster]
) -> tuple[Point, ...] | None:
    """이미 채택한 ROI와 겹치면 줄여 본다. 그래도 겹치면 `None`."""
    candidate = polygon
    for _ in range(OVERLAP_SHRINK_ATTEMPTS):
        if not any(_overlaps(candidate, other.polygon) for other in accepted):
            return candidate
        candidate = _shrink(candidate, OVERLAP_SHRINK_FACTOR)
    return None


def _overlaps(first: tuple[Point, ...], second: tuple[Point, ...]) -> bool:
    """볼록 다각형 둘의 겹침을 분리축 정리로 판정한다."""
    for polygon in (first, second):
        for index in range(len(polygon)):
            start = polygon[index]
            end = polygon[(index + 1) % len(polygon)]
            axis_x = -(end.y - start.y)
            axis_y = end.x - start.x
            first_min, first_max = _project(first, axis_x, axis_y)
            second_min, second_max = _project(second, axis_x, axis_y)
            if first_max <= second_min or second_max <= first_min:
                return False
    return True


def _project(polygon: tuple[Point, ...], axis_x: float, axis_y: float) -> tuple[float, float]:
    values = [axis_x * point.x + axis_y * point.y for point in polygon]
    return min(values), max(values)


def _shrink(polygon: tuple[Point, ...], factor: float) -> tuple[Point, ...]:
    center_x = sum(point.x for point in polygon) / len(polygon)
    center_y = sum(point.y for point in polygon) / len(polygon)
    return tuple(
        Point(
            x=center_x + (point.x - center_x) * factor,
            y=center_y + (point.y - center_y) * factor,
        )
        for point in polygon
    )


def _count_inside(centers: Sequence[tuple[float, float]], polygon: tuple[Point, ...]) -> int:
    return sum(1 for x, y in centers if _contains(polygon, x, y))


def _contains(polygon: tuple[Point, ...], x: float, y: float) -> bool:
    """볼록 다각형 포함 판정. `_convex_hull`이 반시계 방향으로 돌려준다."""
    for index in range(len(polygon)):
        start = polygon[index]
        end = polygon[(index + 1) % len(polygon)]
        if (end.x - start.x) * (y - start.y) - (end.y - start.y) * (x - start.x) < 0:
            return False
    return True


def _reading_order(polygon: tuple[Point, ...]) -> tuple[float, float]:
    center_y = sum(point.y for point in polygon) / len(polygon)
    center_x = sum(point.x for point in polygon) / len(polygon)
    return (center_y, center_x)
