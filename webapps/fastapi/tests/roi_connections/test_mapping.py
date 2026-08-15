"""bbox 중심점과 ROI polygon의 결정적 좌석 매핑 테스트."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.roi_connections.mapping import (
    RoiMappingReason,
    find_roi_connection_for_bbox,
    map_bbox_to_roi,
)
from app.roi_connections.models import Point, RoiConnection

NOW = datetime(2026, 8, 15, 1, 0, tzinfo=UTC)


def _connection(
    seat_id: str,
    polygon: tuple[Point, ...],
) -> RoiConnection:
    return RoiConnection(
        classroom_id="room",
        camera_id="camera-a",
        seat_id=seat_id,
        student_id=None,
        polygon=polygon,
        reference_image_revision=0,
        updated_at=NOW,
    )


@pytest.mark.parametrize(
    ("polygon", "bbox"),
    [
        (
            (Point(0.1, 0.1), Point(0.9, 0.1), Point(0.5, 0.9)),
            (450, 400, 550, 500),
        ),
        (
            (Point(0.1, 0.1), Point(0.4, 0.1), Point(0.4, 0.4), Point(0.1, 0.4)),
            (100, 150, 200, 250),
        ),
        (
            (Point(0.1, 0.1), Point(0.4, 0.1), Point(0.4, 0.4), Point(0.1, 0.4)),
            (50, 50, 150, 150),
        ),
    ],
)
def test_triangle_square_and_boundary_points_are_included(
    polygon: tuple[Point, ...], bbox: tuple[int, int, int, int]
) -> None:
    connection = _connection("seat-a", polygon)

    result = find_roi_connection_for_bbox(
        bbox,
        frame_width_pixels=1000,
        frame_height_pixels=1000,
        connections=[connection],
    )

    assert result == connection


def test_point_outside_all_polygons_returns_none() -> None:
    connection = _connection(
        "seat-a",
        (Point(0.1, 0.1), Point(0.3, 0.1), Point(0.3, 0.3), Point(0.1, 0.3)),
    )

    assert (
        find_roi_connection_for_bbox(
            (700, 700, 800, 800),
            frame_width_pixels=1000,
            frame_height_pixels=1000,
            connections=[connection],
        )
        is None
    )


def test_overlapping_polygons_return_none() -> None:
    polygon = (Point(0.1, 0.1), Point(0.6, 0.1), Point(0.6, 0.6), Point(0.1, 0.6))

    assert (
        find_roi_connection_for_bbox(
            (200, 200, 300, 300),
            frame_width_pixels=1000,
            frame_height_pixels=1000,
            connections=[_connection("seat-a", polygon), _connection("seat-b", polygon)],
        )
        is None
    )
    diagnostic = map_bbox_to_roi(
        (200, 200, 300, 300),
        frame_width_pixels=1000,
        frame_height_pixels=1000,
        connections=[_connection("seat-a", polygon), _connection("seat-b", polygon)],
    )
    assert diagnostic.connection is None
    assert diagnostic.reason == RoiMappingReason.AMBIGUOUS


@pytest.mark.parametrize(
    ("bbox", "width", "height"),
    [
        ((0, 0, 10, 10), 0, 1000),
        ((0, 0, 10, 10), 1000, 0),
        ((10, 10, 10, 20), 1000, 1000),
        ((-1, 10, 20, 30), 1000, 1000),
        ((900, 900, 1100, 1000), 1000, 1000),
    ],
)
def test_invalid_frame_or_bbox_returns_none(
    bbox: tuple[int, int, int, int], width: int, height: int
) -> None:
    connection = _connection(
        "seat-a",
        (Point(0.0, 0.0), Point(1.0, 0.0), Point(1.0, 1.0), Point(0.0, 1.0)),
    )

    assert (
        find_roi_connection_for_bbox(
            bbox,
            frame_width_pixels=width,
            frame_height_pixels=height,
            connections=[connection],
        )
        is None
    )
