"""추론 입출력 타입.

Frame은 워커마다 따로 정의하지 않고 shared에서 가져온다. 같은 것에 이름이
둘 생기면 stream이 넘긴 배열과 inference가 기대하는 배열이 같은 것인지
타입만 보고 알 수 없다.
"""

from __future__ import annotations

from dataclasses import dataclass

from shared.types import Frame

BBox = tuple[int, int, int, int]

__all__ = ["BBox", "Detection", "Frame", "InferenceResult"]


@dataclass(frozen=True)
class Detection:
    class_id: int
    class_name: str
    confidence: float
    bbox: BBox


@dataclass(frozen=True)
class InferenceResult:
    frame_shape: tuple[int, int, int]
    detections: tuple[Detection, ...]
