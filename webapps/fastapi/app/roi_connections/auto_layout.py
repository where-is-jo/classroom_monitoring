"""좌석 행·열 격자를 캡처 화면 좌표로 사영해 ROI 후보를 만드는 순수 규칙.

좌석 관리 화면(`/classrooms/{id}/seats`)은 좌석을 **행·열 격자**로 등록한다.
그 격자는 강의실 바닥이라는 하나의 평면 위에 있고, 카메라는 그 평면을 한 방향에서
본다. 평면과 평면 사이의 대응은 호모그래피(3x3 사영 변환) 하나로 표현되므로,
관리자가 좌석 구역의 네 모서리만 찍으면 나머지 좌석의 자리는 계산으로 나온다.

**탐지 결과를 쓰지 않는다.** 사람이 앉아 있어야 좌석을 알아낼 수 있는 방식은 아무도
없는 시간에 동작하지 않고, 군집이 어느 `seat_id`인지도 알 수 없다. 여기서는 이미
관리자가 등록해 둔 격자 좌표만 입력으로 쓴다.

이 모듈은 순수 계산만 한다 — 저장소도 FastAPI도 알지 못한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .errors import RoiConnectionInputError
from .models import AutoRoiOutcome, Point

# 좌석 구역을 정의하는 데 필요한 모서리 수. 사영 변환의 자유도가 8이라 점 4개
# (좌표 8개)에서 정확히 하나로 정해진다.
GRID_CORNER_COUNT = 4

# 네 점이 한 직선에 가까우면 방정식이 풀리지 않는다. 부동소수 오차와 구분할 수 있는
# 크기로 잡았다.
_SINGULAR_PIVOT = 1e-10

# 좌석 칸을 얼마나 채울지의 기본값. 칸을 가득 채우면 이웃 ROI와 변이 맞닿아, 좌석
# 경계에 걸친 bbox 중심이 어느 쪽으로 갈지 좌표 오차가 정한다. 조금 줄여 사이를 띄운다.
# **실측으로 정한 값이 아니다.** 관리자가 미리보기를 보며 요청마다 바꿀 수 있다.
DEFAULT_SEAT_FILL_RATIO = 0.8

# 이보다 작게 사영된 칸은 만들지 않는다. 화면 넓이 대비 비율이며 1280x1944 프레임에서
# 약 500px^2(가로세로 22px 남짓)에 해당한다. 그보다 작으면 관리자가 화면에서 확인할 수도,
# 사람 bbox 중심이 안정적으로 들어올 수도 없다.
MIN_AUTO_POLYGON_AREA = 0.0002


@dataclass(frozen=True)
class SeatGridCell:
    """격자 위 좌석 한 칸. 좌표가 없는 좌석도 결과에 남기려고 `None`을 허용한다."""

    seat_id: str
    row: int | None
    column: int | None


@dataclass(frozen=True)
class AutoRoiCandidate:
    seat_id: str
    outcome: AutoRoiOutcome
    polygon: tuple[Point, ...] | None = None


@dataclass(frozen=True)
class AutoRoiPlan:
    """자동 생성 계획. 저장하기 전에 그대로 미리보기로 보여줄 수 있다."""

    candidates: tuple[AutoRoiCandidate, ...]
    grid_rows: int
    grid_columns: int

    @property
    def generated(self) -> tuple[AutoRoiCandidate, ...]:
        return tuple(
            candidate
            for candidate in self.candidates
            if candidate.outcome is AutoRoiOutcome.GENERATED
        )


@dataclass(frozen=True)
class Homography:
    """단위 정사각형을 화면 사각형으로 보내는 사영 변환.

    행 우선 3x3에서 마지막 성분을 1로 고정한 8개 값이다. 사영 변환은 스칼라 배를
    구분하지 않으므로 이 형태로 잃는 것이 없다.
    """

    values: tuple[float, float, float, float, float, float, float, float]

    def apply(self, u: float, v: float) -> Point:
        a, b, c, d, e, f, g, h = self.values
        denominator = g * u + h * v + 1.0
        if abs(denominator) < _SINGULAR_PIVOT:
            # 화면 밖 무한원으로 가는 점이다. 좌석 구역이 볼록이면 그 안에서는
            # 생기지 않지만, 계산이 조용히 틀리는 것보다 막고 알리는 편이 낫다.
            raise RoiConnectionInputError("좌석 구역이 화면에 담기지 않는 형태입니다.")
        return Point(
            x=(a * u + b * v + c) / denominator,
            y=(d * u + e * v + f) / denominator,
        )


def fit_grid_homography(corners: Sequence[Point]) -> Homography:
    """좌석 구역 네 모서리에서 사영 변환을 구한다.

    단위 정사각형의 `(0,0) (1,0) (1,1) (0,1)`이 받은 순서의 모서리에 대응한다.
    화면 쪽 좌표는 0~1로 정규화된 값이라 카메라 해상도가 바뀌어도 그대로 쓸 수 있다.
    """
    if len(corners) != GRID_CORNER_COUNT:
        raise RoiConnectionInputError("좌석 구역은 모서리 4곳으로 지정해야 합니다.")
    if any(not 0 <= corner.x <= 1 or not 0 <= corner.y <= 1 for corner in corners):
        raise RoiConnectionInputError("좌석 구역 좌표는 0과 1 사이여야 합니다.")
    if not _is_convex_quadrilateral(corners):
        raise RoiConnectionInputError(
            "좌석 구역은 꼬이지 않은 볼록 사각형이어야 합니다. "
            "한 모서리에서 시작해 이웃한 순서로 네 곳을 찍어 주세요."
        )
    unit_square = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
    matrix: list[list[float]] = []
    targets: list[float] = []
    for (u, v), corner in zip(unit_square, corners, strict=True):
        matrix.append([u, v, 1, 0, 0, 0, -u * corner.x, -v * corner.x])
        targets.append(corner.x)
        matrix.append([0, 0, 0, u, v, 1, -u * corner.y, -v * corner.y])
        targets.append(corner.y)
    solution = _solve_linear_system(matrix, targets)
    return Homography(
        values=(
            solution[0],
            solution[1],
            solution[2],
            solution[3],
            solution[4],
            solution[5],
            solution[6],
            solution[7],
        )
    )


def plan_auto_roi(
    *,
    cells: Sequence[SeatGridCell],
    corners: Sequence[Point],
    preserved_seat_ids: frozenset[str],
    seat_fill_ratio: float,
    min_polygon_area: float,
) -> AutoRoiPlan:
    """좌석마다 ROI 후보를 만든다. 저장은 하지 않는다.

    `preserved_seat_ids`는 이미 ROI가 있어 덮어쓰지 않을 좌석이다. 사람이 그린 좌표를
    계산값으로 지우지 않기 위해서다.
    """
    if not 0 < seat_fill_ratio <= 1:
        raise RoiConnectionInputError("좌석 크기 비율은 0보다 크고 1 이하여야 합니다.")
    homography = fit_grid_homography(corners)
    positioned = [cell for cell in cells if cell.row is not None and cell.column is not None]
    rows = max((cell.row for cell in positioned if cell.row is not None), default=0)
    columns = max((cell.column for cell in positioned if cell.column is not None), default=0)

    candidates: list[AutoRoiCandidate] = []
    for cell in sorted(cells, key=_grid_order):
        if cell.row is None or cell.column is None:
            candidates.append(AutoRoiCandidate(cell.seat_id, AutoRoiOutcome.NO_GRID_POSITION))
            continue
        if cell.seat_id in preserved_seat_ids:
            candidates.append(AutoRoiCandidate(cell.seat_id, AutoRoiOutcome.EXISTING_KEPT))
            continue
        polygon = _project_cell(
            homography,
            row=cell.row,
            column=cell.column,
            rows=rows,
            columns=columns,
            seat_fill_ratio=seat_fill_ratio,
        )
        if _polygon_area(polygon) < min_polygon_area:
            candidates.append(AutoRoiCandidate(cell.seat_id, AutoRoiOutcome.TOO_SMALL))
            continue
        candidates.append(AutoRoiCandidate(cell.seat_id, AutoRoiOutcome.GENERATED, polygon=polygon))
    return AutoRoiPlan(candidates=tuple(candidates), grid_rows=rows, grid_columns=columns)


def _grid_order(cell: SeatGridCell) -> tuple[int, int, int, str]:
    """행·열 순으로 정렬한다. 좌표가 없는 좌석은 뒤로 보낸다."""
    if cell.row is None or cell.column is None:
        return (1, 0, 0, cell.seat_id)
    return (0, cell.row, cell.column, cell.seat_id)


def _project_cell(
    homography: Homography,
    *,
    row: int,
    column: int,
    rows: int,
    columns: int,
    seat_fill_ratio: float,
) -> tuple[Point, ...]:
    """격자 한 칸을 화면 좌표 사각형으로 보낸다.

    축소는 **사영 전 격자 공간에서** 한다. 화면에서 줄이면 원근이 깨져 뒤쪽 좌석이
    앞쪽보다 덜 줄어든다.
    """
    cell_width = 1.0 / columns
    cell_height = 1.0 / rows
    center_u = (column - 0.5) * cell_width
    center_v = (row - 0.5) * cell_height
    half_width = cell_width * seat_fill_ratio / 2
    half_height = cell_height * seat_fill_ratio / 2
    cell_corners = (
        (center_u - half_width, center_v - half_height),
        (center_u + half_width, center_v - half_height),
        (center_u + half_width, center_v + half_height),
        (center_u - half_width, center_v + half_height),
    )
    return tuple(_clamped(homography.apply(u, v)) for u, v in cell_corners)


def _clamped(point: Point) -> Point:
    """부동소수 오차로 0~1을 아주 조금 넘는 값을 잘라낸다."""
    return Point(x=min(max(point.x, 0.0), 1.0), y=min(max(point.y, 0.0), 1.0))


def _polygon_area(polygon: Sequence[Point]) -> float:
    return abs(_signed_area(polygon))


def _signed_area(polygon: Sequence[Point]) -> float:
    total = 0.0
    for index, point in enumerate(polygon):
        following = polygon[(index + 1) % len(polygon)]
        total += point.x * following.y - following.x * point.y
    return total / 2


def _is_convex_quadrilateral(corners: Sequence[Point]) -> bool:
    """네 점이 꼬이지 않은 볼록 사각형인지 본다.

    볼록이어야 사각형 안쪽이 격자 안쪽과 일대일로 대응한다. 오목하거나 꼬인 사각형에서는
    사영이 뒤집혀 좌석이 엉뚱한 자리에 생긴다. 시계·반시계 방향은 가리지 않는다 —
    어느 쪽이든 미리보기에서 관리자가 확인한다.
    """
    signs: list[bool] = []
    for index in range(len(corners)):
        first = corners[index]
        second = corners[(index + 1) % len(corners)]
        third = corners[(index + 2) % len(corners)]
        cross = (second.x - first.x) * (third.y - second.y) - (second.y - first.y) * (
            third.x - second.x
        )
        if abs(cross) < _SINGULAR_PIVOT:
            return False
        signs.append(cross > 0)
    return all(signs) or not any(signs)


def _solve_linear_system(matrix: list[list[float]], targets: list[float]) -> list[float]:
    """부분 피벗 가우스 소거법. 미지수 8개짜리라 외부 수치 라이브러리를 들이지 않는다."""
    size = len(targets)
    rows = [[*matrix[index], targets[index]] for index in range(size)]
    for column in range(size):
        pivot_row = max(range(column, size), key=lambda index: abs(rows[index][column]))
        if abs(rows[pivot_row][column]) < _SINGULAR_PIVOT:
            raise RoiConnectionInputError(
                "좌석 구역 모서리가 한 직선에 가까워 화면 보정을 계산할 수 없습니다."
            )
        rows[column], rows[pivot_row] = rows[pivot_row], rows[column]
        pivot = rows[column][column]
        for index in range(column, size + 1):
            rows[column][index] /= pivot
        for index in range(size):
            if index == column:
                continue
            factor = rows[index][column]
            if factor == 0:
                continue
            for position in range(column, size + 1):
                rows[index][position] -= factor * rows[column][position]
    return [row[size] for row in rows]
