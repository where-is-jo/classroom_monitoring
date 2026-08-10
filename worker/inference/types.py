from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

import numpy as np
from numpy.typing import NDArray

Frame = NDArray[np.uint8]
BBox = tuple[int, int, int, int]


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
