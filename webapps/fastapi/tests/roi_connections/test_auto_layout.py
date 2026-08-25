"""좌석 격자를 화면 좌표로 사영하는 자동 ROI 규칙 테스트.

카메라도 저장소도 필요 없는 순수 계산이라 여기서 끝까지 검증한다.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.roi_connections.auto_layout import (
    AutoRoiPlan,
    SeatGridCell,
    fit_grid_homography,
    plan_auto_roi,
)
from app.roi_connections.errors import RoiConnectionInputError
from app.roi_connections.mapping import map_bbox_to_roi
from app.roi_connections.models import AutoRoiOutcome, Point, RoiConnection

# 화면 전체를 좌석 구역으로 잡은 사각형. 사영이 항등에 가까워 기대값을 손으로 쓸 수 있다.
FULL_FRAME = (Point(0, 0), Point(1, 0), Point(1, 1), Point(0, 1))

# 원근이 있는 사각형. 위쪽(먼 줄)이 좁다.
TRAPEZOID = (Point(0.3, 0.2), Point(0.7, 0.2), Point(0.95, 0.9), Point(0.05, 0.9))

MIN_AREA = 0.0002


def _cells(*positions: tuple[int, int]) -> list[SeatGridCell]:
    return [
        SeatGridCell(seat_id=f"seat-{row}-{column}", row=row, column=column)
        for row, column in positions
    ]


def _area(polygon: tuple[Point, ...]) -> float:
    total = 0.0
    for index, point in enumerate(polygon):
        following = polygon[(index + 1) % len(polygon)]
        total += point.x * following.y - following.x * point.y
    return abs(total) / 2


def _center(polygon: tuple[Point, ...]) -> Point:
    return Point(
        x=sum(point.x for point in polygon) / len(polygon),
        y=sum(point.y for point in polygon) / len(polygon),
    )


def _connection(seat_id: str, polygon: tuple[Point, ...]) -> RoiConnection:
    return RoiConnection(
        classroom_id="room",
        camera_id="camera-a",
        seat_id=seat_id,
        student_id=None,
        polygon=polygon,
        reference_image_revision=1,
        updated_at=datetime(2026, 8, 24, tzinfo=UTC),
    )


def _covers(polygon: tuple[Point, ...], point: Point) -> bool:
    """판정 경로와 같은 순수 함수로 "이 점이 이 ROI 안인가"를 본다."""
    scale = 100_000
    center_x, center_y = int(point.x * scale), int(point.y * scale)
    result = map_bbox_to_roi(
        (center_x - 1, center_y - 1, center_x + 1, center_y + 1),
        frame_width_pixels=scale,
        frame_height_pixels=scale,
        connections=[_connection("probe", polygon)],
    )
    return result.connection is not None


def _plan(
    cells: list[SeatGridCell],
    *,
    corners: tuple[Point, ...] = FULL_FRAME,
    preserved: frozenset[str] = frozenset(),
    fill: float = 1.0,
    min_area: float = MIN_AREA,
) -> AutoRoiPlan:
    return plan_auto_roi(
        cells=cells,
        corners=corners,
        preserved_seat_ids=preserved,
        seat_fill_ratio=fill,
        min_polygon_area=min_area,
    )


def test_homography_maps_unit_square_corners_onto_given_quad() -> None:
    """네 모서리가 정확히 재현되지 않으면 그 위에 얹는 좌석도 전부 어긋난다."""
    homography = fit_grid_homography(TRAPEZOID)

    for (u, v), expected in zip(((0, 0), (1, 0), (1, 1), (0, 1)), TRAPEZOID, strict=True):
        projected = homography.apply(u, v)
        assert projected.x == pytest.approx(expected.x, abs=1e-9)
        assert projected.y == pytest.approx(expected.y, abs=1e-9)


def test_full_frame_two_by_two_grid_becomes_quadrants() -> None:
    """화면 전체를 2x2로 잡으면 좌석이 사분면 하나씩을 차지한다."""
    plan = _plan(_cells((1, 1), (1, 2), (2, 1), (2, 2)))

    assert plan.grid_rows == 2
    assert plan.grid_columns == 2
    assert [candidate.seat_id for candidate in plan.candidates] == [
        "seat-1-1",
        "seat-1-2",
        "seat-2-1",
        "seat-2-2",
    ]
    first = plan.candidates[0].polygon
    assert first is not None
    assert first[0] == Point(0.0, 0.0)
    assert first[2].x == pytest.approx(0.5)
    assert first[2].y == pytest.approx(0.5)
    for candidate in plan.candidates:
        assert candidate.outcome is AutoRoiOutcome.GENERATED
        assert candidate.polygon is not None
        assert _area(candidate.polygon) == pytest.approx(0.25)


def test_generated_polygons_stay_inside_the_frame_and_do_not_overlap() -> None:
    """겹치는 ROI는 판정에서 AMBIGUOUS가 되어 좌석을 정하지 못한다(결정 0020의 4번)."""
    plan = _plan(_cells(*[(row, column) for row in range(1, 4) for column in range(1, 5)]))

    connections = [
        _connection(candidate.seat_id, candidate.polygon or ()) for candidate in plan.generated
    ]
    assert len(connections) == 12
    for candidate in plan.generated:
        assert candidate.polygon is not None
        for point in candidate.polygon:
            assert 0 <= point.x <= 1
            assert 0 <= point.y <= 1

    # 각 좌석 ROI의 중심에 bbox를 놓으면 정확히 그 좌석 하나로 매핑되어야 한다.
    for candidate in plan.generated:
        assert candidate.polygon is not None
        center = _center(candidate.polygon)
        bbox = (
            int(center.x * 1000) - 2,
            int(center.y * 1000) - 2,
            int(center.x * 1000) + 2,
            int(center.y * 1000) + 2,
        )
        result = map_bbox_to_roi(
            bbox,
            frame_width_pixels=1000,
            frame_height_pixels=1000,
            connections=connections,
        )
        assert result.connection is not None
        assert result.connection.seat_id == candidate.seat_id


def test_perspective_makes_far_rows_smaller_than_near_rows() -> None:
    """사다리꼴에서 뒷줄이 앞줄보다 작아야 원근이 반영된 것이다."""
    plan = _plan(_cells((1, 1), (2, 1)), corners=TRAPEZOID)

    far, near = plan.candidates[0].polygon, plan.candidates[1].polygon
    assert far is not None and near is not None
    assert _area(far) < _area(near)


def test_fill_ratio_shrinks_area_but_keeps_the_seat_in_place() -> None:
    """좌석 사이 간격을 주려고 줄여도 자리 자체는 움직이면 안 된다.

    "자리"의 기준은 폴리곤 무게중심이 아니라 **격자 칸 중심을 사영한 점**이다.
    원근이 있으면 사다리꼴의 무게중심은 넓은 쪽으로 끌리므로, 축소 비율에 따라
    무게중심은 움직인다. 판정이 보는 것은 그 점이 ROI 안에 있는지이므로
    (`map_bbox_to_roi`), 그 점이 계속 들어 있는지로 확인한다.
    """
    homography = fit_grid_homography(TRAPEZOID)
    seat_centers = {
        "seat-1-1": homography.apply(0.25, 0.5),
        "seat-1-2": homography.apply(0.75, 0.5),
    }
    full = _plan(_cells((1, 1), (1, 2)), corners=TRAPEZOID, fill=1.0)
    shrunk = _plan(_cells((1, 1), (1, 2)), corners=TRAPEZOID, fill=0.5)

    for full_candidate, shrunk_candidate in zip(full.candidates, shrunk.candidates, strict=True):
        assert full_candidate.polygon is not None
        assert shrunk_candidate.polygon is not None
        assert _area(shrunk_candidate.polygon) < _area(full_candidate.polygon)
        center = seat_centers[shrunk_candidate.seat_id]
        assert _covers(shrunk_candidate.polygon, center)
        assert _covers(full_candidate.polygon, center)


def test_existing_roi_is_never_overwritten() -> None:
    """사람이 그린 좌표를 계산값으로 지우지 않는다."""
    plan = _plan(_cells((1, 1), (1, 2)), preserved=frozenset({"seat-1-1"}))

    kept, generated = plan.candidates
    assert kept.outcome is AutoRoiOutcome.EXISTING_KEPT
    assert kept.polygon is None
    assert generated.outcome is AutoRoiOutcome.GENERATED


def test_seat_without_grid_position_is_reported_not_guessed() -> None:
    """행·열이 없으면 자리를 추정하지 않는다. 미관측을 관측으로 바꾸지 않는다."""
    cells = [*_cells((1, 1)), SeatGridCell(seat_id="seat-legacy", row=None, column=None)]

    plan = _plan(cells)

    outcomes = {candidate.seat_id: candidate.outcome for candidate in plan.candidates}
    assert outcomes["seat-legacy"] is AutoRoiOutcome.NO_GRID_POSITION
    # 좌표 없는 좌석은 격자 크기 계산에도 끼지 않는다.
    assert plan.grid_rows == 1
    assert plan.grid_columns == 1


def test_cells_that_project_too_small_are_reported_instead_of_saved() -> None:
    """몇 픽셀짜리 ROI는 관리자가 확인할 수 없어 만들지 않는다."""
    plan = _plan(_cells((1, 1)), min_area=0.5, fill=0.5)

    assert plan.candidates[0].outcome is AutoRoiOutcome.TOO_SMALL
    assert plan.candidates[0].polygon is None


def test_collinear_corners_are_rejected() -> None:
    corners = (Point(0.1, 0.5), Point(0.4, 0.5), Point(0.7, 0.5), Point(0.9, 0.5))

    with pytest.raises(RoiConnectionInputError):
        fit_grid_homography(corners)


def test_self_crossing_quad_is_rejected() -> None:
    """꼬인 사각형은 사영이 뒤집혀 좌석이 엉뚱한 자리에 생긴다."""
    corners = (Point(0.1, 0.1), Point(0.9, 0.1), Point(0.1, 0.9), Point(0.9, 0.9))

    with pytest.raises(RoiConnectionInputError):
        fit_grid_homography(corners)


def test_concave_quad_is_rejected() -> None:
    corners = (Point(0.1, 0.1), Point(0.9, 0.1), Point(0.5, 0.5), Point(0.1, 0.9))

    with pytest.raises(RoiConnectionInputError):
        fit_grid_homography(corners)


def test_corner_count_and_range_are_checked() -> None:
    with pytest.raises(RoiConnectionInputError):
        fit_grid_homography((Point(0, 0), Point(1, 0), Point(1, 1)))
    with pytest.raises(RoiConnectionInputError):
        fit_grid_homography((Point(0, 0), Point(1.5, 0), Point(1, 1), Point(0, 1)))


def test_fill_ratio_out_of_range_is_rejected() -> None:
    with pytest.raises(RoiConnectionInputError):
        _plan(_cells((1, 1)), fill=0.0)
    with pytest.raises(RoiConnectionInputError):
        _plan(_cells((1, 1)), fill=1.5)


def test_counter_clockwise_corners_still_produce_a_usable_grid() -> None:
    """모서리를 반대 방향으로 찍어도 계산은 성립한다. 방향은 미리보기로 확인한다."""
    counter_clockwise = (Point(0, 0), Point(0, 1), Point(1, 1), Point(1, 0))

    plan = _plan(_cells((1, 1), (1, 2)), corners=counter_clockwise)

    assert all(candidate.outcome is AutoRoiOutcome.GENERATED for candidate in plan.candidates)
    first = plan.candidates[0].polygon
    assert first is not None
    # 열 방향이 화면의 세로축으로 간다 — 찍은 순서가 그대로 격자 축이 된다.
    assert _center(first).y == pytest.approx(0.25)
