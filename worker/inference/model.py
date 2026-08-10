from __future__ import annotations

import importlib
from typing import Any

import numpy as np
from numpy.typing import NDArray

from .types import Detection, Frame, InferenceResult

try:
    from ultralytics import YOLO
except ImportError:  # pragma: no cover
    YOLO = None  # type: ignore[assignment]

TARGET_CLASS_IDS: dict[int, str] = {0: "person", 67: "cell phone"}
DEFAULT_MODEL_PATH = "yolov8n.pt"


def _to_scalar(value: Any) -> float:
    array = np.asarray(value)
    if array.size == 0:
        raise ValueError("박스 정보가 비어 있습니다.")
    return float(array.flatten()[0])


def _to_int(value: Any) -> int:
    return int(_to_scalar(value))


class Yolo8nDetector:
    """YOLOv8n 모델로 프레임에서 탐지를 수행한다."""

    def __init__(
        self,
        *,
        model_path: str = DEFAULT_MODEL_PATH,
        device: str = "cpu",
        confidence_threshold: float = 0.25,
        model: Any | None = None,
        target_class_ids: dict[int, str] | None = None,
    ) -> None:
        self._device = device
        self._confidence_threshold = confidence_threshold
        self._target_class_ids = target_class_ids or TARGET_CLASS_IDS

        if model is not None:
            self._model = model
        else:
            if YOLO is None:
                raise ImportError(
                    "ultralytics 패키지가 설치되어 있지 않습니다. "
                    "worker/inference/requirements.txt의 의존성을 설치하세요."
                )
            self._model = YOLO(model_path)
            try:
                self._model.to(self._device)
            except AttributeError:
                pass

    def detect(self, frame: Frame) -> InferenceResult:
        if frame.ndim != 3 or frame.shape[2] != 3:
            raise ValueError("frame은 HxWx3 형태의 uint8 배열이어야 합니다.")
        if frame.dtype != np.uint8:
            raise ValueError("frame은 uint8 dtype이어야 합니다.")

        results = self._model(
            frame,
            device=self._device,
            conf=self._confidence_threshold,
            classes=list(self._target_class_ids),
        )

        detections: list[Detection] = []
        for result in results:
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue

            for box in boxes:
                class_id = _to_int(getattr(box, "cls", getattr(box, "class", 0)))
                if class_id not in self._target_class_ids:
                    continue
                confidence = float(_to_scalar(getattr(box, "conf", getattr(box, "confidence", 0.0))))
                xyxy = np.asarray(getattr(box, "xyxy", getattr(box, "xyxys", None)))
                if xyxy is None or xyxy.size < 4:
                    continue
                xyxy = xyxy.reshape(-1)[:4].tolist()
                bbox = tuple(int(coord) for coord in xyxy)

                detections.append(
                    Detection(
                        class_id=class_id,
                        class_name=self._target_class_ids[class_id],
                        confidence=confidence,
                        bbox=bbox,
                    )
                )

        return InferenceResult(frame_shape=frame.shape, detections=tuple(detections))
