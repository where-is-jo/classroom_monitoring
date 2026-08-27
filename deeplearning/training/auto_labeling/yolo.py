from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from .errors import AutoLabelingError

# labelImg와 이 도구는 중심점·크기를 제한된 소수 자릿수로 저장한다. 이미지 경계에
# 정확히 닿는 box는 각 값을 따로 반올림하면서 edge가 최대 수십억분의 몇만큼 범위를
# 벗어날 수 있으므로, 한 픽셀보다 훨씬 작은 직렬화 오차만 허용한다.
BOUNDARY_SERIALIZATION_EPSILON = 1e-6


@dataclass(frozen=True)
class YoloBox:
    class_id: int
    center_x: float
    center_y: float
    width: float
    height: float

    @property
    def xyxy(self) -> tuple[float, float, float, float]:
        return (
            self.center_x - self.width / 2,
            self.center_y - self.height / 2,
            self.center_x + self.width / 2,
            self.center_y + self.height / 2,
        )


def parse_yolo_file(
    path: Path, *, reject_exact_duplicates: bool = False
) -> list[YoloBox]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise AutoLabelingError(f"라벨 파일을 읽을 수 없습니다: {path.name}") from exc
    boxes: list[YoloBox] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 5:
            raise AutoLabelingError(
                f"{path.name} {line_number}번째 줄은 YOLO 값 5개여야 합니다."
            )
        try:
            class_id = int(parts[0])
            center_x, center_y, width, height = map(float, parts[1:])
        except ValueError as exc:
            raise AutoLabelingError(
                f"{path.name} {line_number}번째 줄에 잘못된 숫자가 있습니다."
            ) from exc
        box = YoloBox(class_id, center_x, center_y, width, height)
        validate_yolo_box(box, path.name, line_number)
        if reject_exact_duplicates and box in boxes:
            raise AutoLabelingError(
                f"{path.name} {line_number}번째 bbox가 앞선 bbox와 완전히 중복됐습니다."
            )
        boxes.append(box)
    return boxes


def validate_yolo_box(box: YoloBox, file_name: str, line_number: int) -> None:
    if box.class_id != 0:
        raise AutoLabelingError(
            f"{file_name} {line_number}번째 줄의 class ID는 0이어야 합니다."
        )
    values = (box.center_x, box.center_y, box.width, box.height)
    if not all(math.isfinite(value) for value in values):
        raise AutoLabelingError(
            f"{file_name} {line_number}번째 bbox 값은 유한수여야 합니다."
        )
    if box.width <= 0 or box.height <= 0:
        raise AutoLabelingError(
            f"{file_name} {line_number}번째 bbox 크기는 0보다 커야 합니다."
        )
    if any(value < 0 or value > 1 for value in values):
        raise AutoLabelingError(
            f"{file_name} {line_number}번째 bbox 값은 0~1이어야 합니다."
        )
    left, top, right, bottom = box.xyxy
    epsilon = BOUNDARY_SERIALIZATION_EPSILON
    if left < -epsilon or top < -epsilon or right > 1 + epsilon or bottom > 1 + epsilon:
        raise AutoLabelingError(
            f"{file_name} {line_number}번째 bbox가 이미지 범위를 벗어납니다."
        )


def write_yolo_file(path: Path, boxes: list[YoloBox]) -> None:
    if len(set(boxes)) != len(boxes):
        raise AutoLabelingError(f"{path.name}에 완전히 중복된 bbox가 있습니다.")
    for index, box in enumerate(boxes, start=1):
        validate_yolo_box(box, path.name, index)
    text = "".join(
        f"{box.class_id} {box.center_x:.8f} {box.center_y:.8f} "
        f"{box.width:.8f} {box.height:.8f}\n"
        for box in boxes
    )
    path.write_text(text, encoding="utf-8", newline="\n")


def iou(first: YoloBox, second: YoloBox) -> float:
    first_left, first_top, first_right, first_bottom = first.xyxy
    second_left, second_top, second_right, second_bottom = second.xyxy
    intersection_width = max(
        0.0, min(first_right, second_right) - max(first_left, second_left)
    )
    intersection_height = max(
        0.0, min(first_bottom, second_bottom) - max(first_top, second_top)
    )
    intersection = intersection_width * intersection_height
    first_area = first.width * first.height
    second_area = second.width * second.height
    union = first_area + second_area - intersection
    return 0.0 if union <= 0 else intersection / union


def touches_boundary(box: YoloBox, *, epsilon: float = 1e-6) -> bool:
    left, top, right, bottom = box.xyxy
    return (
        left <= epsilon
        or top <= epsilon
        or right >= 1 - epsilon
        or bottom >= 1 - epsilon
    )
