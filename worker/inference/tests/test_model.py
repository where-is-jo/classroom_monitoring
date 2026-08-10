from __future__ import annotations

import numpy as np

from ..model import Yolo8nDetector
from ..types import Frame


class FakeBox:
    def __init__(self, xyxy: list[float], cls: int, conf: float) -> None:
        self.xyxy = np.array(xyxy, dtype=float)
        self.cls = cls
        self.conf = conf


class FakeResult:
    def __init__(self, boxes: list[FakeBox]) -> None:
        self.boxes = boxes


class FakeModel:
    def __init__(self, results: list[FakeResult]) -> None:
        self._results = results

    def __call__(self, frame: Frame, *, device: str, conf: float, classes: list[int]):
        assert frame.shape == (3, 4, 3) or frame.shape == (4, 3, 3)
        assert conf == 0.5
        assert classes == [0, 67]
        return self._results


def make_frame() -> Frame:
    return np.zeros((3, 4, 3), dtype=np.uint8)


def test_detect_returns_target_detections() -> None:
    fake_boxes = [FakeBox([1.0, 2.0, 3.0, 4.0], cls=0, conf=0.75)]
    fake_model = FakeModel([FakeResult(fake_boxes)])
    detector = Yolo8nDetector(
        model_path="dummy.pt",
        device="cpu",
        confidence_threshold=0.5,
        model=fake_model,
    )

    result = detector.detect(make_frame())

    assert result.frame_shape == (3, 4, 3)
    assert len(result.detections) == 1
    assert result.detections[0].class_name == "person"
    assert result.detections[0].confidence == 0.75
    assert result.detections[0].bbox == (1, 2, 3, 4)
