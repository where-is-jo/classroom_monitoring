from __future__ import annotations

from pathlib import Path

import pytest

from auto_labeling.errors import AutoLabelingError
from auto_labeling.yolo import YoloBox, iou, parse_yolo_file, write_yolo_file


def test_yolo_round_trip_supports_empty_and_person_boxes(tmp_path: Path) -> None:
    empty_path = tmp_path / "empty.txt"
    write_yolo_file(empty_path, [])
    assert parse_yolo_file(empty_path) == []

    label_path = tmp_path / "person.txt"
    expected = [YoloBox(0, 0.5, 0.5, 0.25, 0.5)]
    write_yolo_file(label_path, expected)
    actual = parse_yolo_file(label_path)

    assert actual == expected


def test_yolo_round_trip_accepts_boundary_box_serialization_error(
    tmp_path: Path,
) -> None:
    label_path = tmp_path / "boundary.txt"
    expected = [
        YoloBox(
            0,
            0.577902889251709,
            0.4860034942626953,
            0.31908817291259767,
            0.9720069885253906,
        )
    ]

    write_yolo_file(label_path, expected)
    actual = parse_yolo_file(label_path)

    assert len(actual) == 1
    assert actual[0].xyxy[1] >= -1e-6


@pytest.mark.parametrize(
    "content, message",
    [
        ("1 0.5 0.5 0.2 0.2\n", "class ID"),
        ("0 0.0 0.5 0.2 0.2\n", "범위를 벗어납니다"),
        ("0 0.5 0.5 0 0.2\n", "0보다 커야"),
        ("0 0.5 0.5 0.2\n", "값 5개"),
        ("0 nan 0.5 0.2 0.2\n", "유한수"),
    ],
)
def test_invalid_yolo_labels_are_rejected(
    tmp_path: Path, content: str, message: str
) -> None:
    label_path = tmp_path / "invalid.txt"
    label_path.write_text(content, encoding="utf-8")

    with pytest.raises(AutoLabelingError, match=message):
        parse_yolo_file(label_path)


def test_iou_matches_identical_and_disjoint_boxes() -> None:
    first = YoloBox(0, 0.5, 0.5, 0.2, 0.2)
    second = YoloBox(0, 0.5, 0.5, 0.2, 0.2)
    third = YoloBox(0, 0.1, 0.1, 0.1, 0.1)

    assert iou(first, second) == pytest.approx(1.0)
    assert iou(first, third) == 0.0
