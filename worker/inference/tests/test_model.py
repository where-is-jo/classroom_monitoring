from __future__ import annotations

import numpy as np

from ..model import DEFAULT_IMAGE_SIZE, Yolo8nDetector
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

    def __call__(
        self, frame: Frame, *, device: str, conf: float, imgsz: int, classes: list[int]
    ):
        assert frame.shape == (3, 4, 3) or frame.shape == (4, 3, 3)
        assert conf == 0.5
        assert classes == [0, 67]
        # 입력 크기를 넘기지 않으면 ultralytics가 640으로 줄여 뒤쪽 사람을 놓친다.
        self.received_imgsz = imgsz
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


def test_기본_입력_크기는_ultralytics_기본값_640이_아니다() -> None:
    """CCTV 원본이 640보다 크면 축소로 뒤쪽에 앉은 사람이 탐지에서 빠진다.

    실측(3A컴퓨터실 1280x1944): 640은 신뢰도 0.6 이상이 프레임당 4.4명,
    1280은 7.6명이었다. 기본값을 명시하지 않으면 이 손실이 조용히 생긴다.
    """
    fake_model = FakeModel([FakeResult([])])
    detector = Yolo8nDetector(model_path="dummy.pt", confidence_threshold=0.5, model=fake_model)

    detector.detect(make_frame())

    assert fake_model.received_imgsz == DEFAULT_IMAGE_SIZE
    assert DEFAULT_IMAGE_SIZE != 640


def test_설정한_입력_크기가_모델까지_전달된다() -> None:
    fake_model = FakeModel([FakeResult([])])
    detector = Yolo8nDetector(
        model_path="dummy.pt",
        confidence_threshold=0.5,
        image_size=960,
        model=fake_model,
    )

    detector.detect(make_frame())

    assert fake_model.received_imgsz == 960
