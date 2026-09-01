from __future__ import annotations

import json

import pytest

from ..handover_calibration import build_route_json, normalize_pixel_rect


def test_픽셀_문_영역을_정규화한다() -> None:
    assert normalize_pixel_rect((100, 200, 300, 400), (1000, 2000, 3)) == (
        0.05,
        0.2,
        0.2,
        0.6,
    )


@pytest.mark.parametrize(
    "rect",
    [
        (0, 0, 0, 10),
        (-1, 0, 10, 10),
        (95, 0, 10, 10),
        (0, 95, 10, 10),
    ],
)
def test_프레임_밖이거나_넓이_없는_문_영역을_거부한다(
    rect: tuple[int, int, int, int],
) -> None:
    with pytest.raises(ValueError, match="프레임 안"):
        normalize_pixel_rect(rect, (100, 100, 3))


def test_환경변수용_route_JSON을_만든다() -> None:
    value = build_route_json(
        entry_camera_id="entry-camera",
        classroom_camera_id="classroom-cctv",
        zone=(0.0, 0.1, 0.3, 1.0),
    )

    assert json.loads(value) == [
        {
            "entry_camera_id": "entry-camera",
            "classroom_camera_id": "classroom-cctv",
            "classroom_entry_zone": [0.0, 0.1, 0.3, 1.0],
        }
    ]
