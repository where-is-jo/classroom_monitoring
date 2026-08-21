"""사람 크롭에서 교차 카메라 ReID 특징을 추출한다."""

from __future__ import annotations

import ctypes
import os
from collections import defaultdict, deque
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .homecam_tracking import PersonTrack


def normalize_feature(value: Any) -> np.ndarray:
    feature = np.asarray(value, dtype=np.float32).reshape(-1)
    if feature.size != 512 or not np.isfinite(feature).all():
        raise ValueError("ReID 특징은 유한한 512차원 벡터여야 합니다.")
    norm = float(np.linalg.norm(feature))
    if norm <= 1e-12:
        raise ValueError("ReID 특징 norm이 0입니다.")
    return feature / norm


class PersonReIdEngine:
    """OSNet-AIN ONNX 모델을 한 번 로드해 512차원 특징을 추출한다."""

    def __init__(
        self,
        model_path: str | Path,
        *,
        providers: Sequence[str] = ("CUDAExecutionProvider", "CPUExecutionProvider"),
        session: Any | None = None,
    ) -> None:
        self._dll_directory_handle: Any | None = None
        self._cudnn_handle: Any | None = None
        if session is None:
            if os.name == "nt" and "CUDAExecutionProvider" in providers:
                import torch

                torch_dll_dir = Path(torch.__file__).resolve().parent / "lib"
                cudnn_dll = torch_dll_dir / "cudnn64_9.dll"
                if cudnn_dll.is_file():
                    os.environ["PATH"] = (
                        f"{torch_dll_dir}{os.pathsep}{os.environ.get('PATH', '')}"
                    )
                    self._dll_directory_handle = os.add_dll_directory(
                        str(torch_dll_dir)
                    )
                    self._cudnn_handle = ctypes.WinDLL(str(cudnn_dll))
            import onnxruntime as ort

            path = Path(model_path).resolve()
            if not path.is_file():
                raise FileNotFoundError(
                    f"OSNet ONNX 모델이 없습니다: {path}. "
                    "prepare_person_reid.py를 먼저 실행하세요."
                )
            session = ort.InferenceSession(str(path), providers=list(providers))
        self._session = session
        self._input_name = session.get_inputs()[0].name

    @property
    def providers(self) -> tuple[str, ...]:
        return tuple(self._session.get_providers())

    @staticmethod
    def preprocess(crop_bgr: np.ndarray) -> np.ndarray:
        if crop_bgr is None or crop_bgr.size == 0:
            raise ValueError("사람 크롭이 비어 있습니다.")
        resized = cv2.resize(crop_bgr, (128, 256), interpolation=cv2.INTER_LINEAR)
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        mean = np.asarray((0.485, 0.456, 0.406), dtype=np.float32)
        std = np.asarray((0.229, 0.224, 0.225), dtype=np.float32)
        normalized = (rgb - mean) / std
        return np.transpose(normalized, (2, 0, 1))[None].astype(np.float32)

    def extract(self, crop_bgr: np.ndarray) -> np.ndarray:
        output = self._session.run(None, {self._input_name: self.preprocess(crop_bgr)})
        if not output:
            raise RuntimeError("OSNet 출력이 비어 있습니다.")
        return normalize_feature(output[0])


class TrackFeatureStore:
    """카메라와 로컬 track ID별 ReID 특징을 품질 가중 평균한다."""

    def __init__(self, *, history_size: int = 8) -> None:
        if history_size < 1:
            raise ValueError("history_size는 1 이상이어야 합니다.")
        self._history_size = history_size
        self._features: dict[tuple[str, int], deque[np.ndarray]] = defaultdict(
            lambda: deque(maxlen=history_size)
        )

    def update(
        self,
        camera_id: str,
        track: PersonTrack,
        frame: np.ndarray,
        engine: PersonReIdEngine,
    ) -> np.ndarray | None:
        height, width = frame.shape[:2]
        left, top, right, bottom = track.bbox
        left, top = max(0, left), max(0, top)
        right, bottom = min(width, right), min(height, bottom)
        if right - left < 16 or bottom - top < 32:
            return self.get(camera_id, track.track_id)
        feature = engine.extract(frame[top:bottom, left:right])
        self._features[(camera_id, track.track_id)].append(feature)
        return self.get(camera_id, track.track_id)

    def get(self, camera_id: str, track_id: int) -> np.ndarray | None:
        values = self._features.get((camera_id, track_id))
        if not values:
            return None
        return normalize_feature(np.mean(np.stack(values), axis=0))

    def retain(self, active_keys: set[tuple[str, int]]) -> None:
        self._features = defaultdict(
            lambda: deque(maxlen=self._history_size),
            {key: value for key, value in self._features.items() if key in active_keys},
        )
