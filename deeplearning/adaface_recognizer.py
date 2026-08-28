"""AdaFace ONNX 모델을 InsightFace recognizer 호환 인터페이스로 감싼다."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

ADAFACE_INPUT_SIZE = 112


def preprocess_aligned_face(aligned_bgr: np.ndarray) -> np.ndarray:
    """공식 AdaFace 입력 계약에 맞는 ``(1, 3, 112, 112)`` 텐서를 만든다."""

    if aligned_bgr.shape != (ADAFACE_INPUT_SIZE, ADAFACE_INPUT_SIZE, 3):
        raise ValueError(
            f"AdaFace 입력은 112x112 3채널 BGR이어야 합니다: {aligned_bgr.shape}"
        )
    # 현재 공식 CVLFace 배포본은 RGB 계약이다. SCRFD/InsightFace 정렬 결과는 BGR이므로
    # 채널을 뒤집은 뒤 mean=std=0.5 정규화를 적용한다.
    aligned_rgb = aligned_bgr[:, :, ::-1]
    normalized = (aligned_rgb.astype(np.float32) / 255.0 - 0.5) / 0.5
    return np.expand_dims(normalized.transpose(2, 0, 1), axis=0).astype(np.float32)


class AdaFaceOnnxRecognizer:
    """InsightFace의 ``prepare``·``get_feat`` 계약을 구현하는 AdaFace 어댑터."""

    def __init__(self, model_path: Path, *, providers: list[str]) -> None:
        import onnxruntime as ort

        self._session = ort.InferenceSession(str(model_path), providers=providers)
        inputs = self._session.get_inputs()
        outputs = self._session.get_outputs()
        if len(inputs) != 1 or not outputs:
            raise RuntimeError("AdaFace ONNX 입출력 개수가 올바르지 않습니다.")
        self._input_name = inputs[0].name
        self._embedding_output_name = outputs[0].name
        self._validate_contract(inputs[0], outputs[0])

    @staticmethod
    def _validate_contract(input_value: Any, output_value: Any) -> None:
        input_shape = tuple(input_value.shape)
        output_shape = tuple(output_value.shape)
        if len(input_shape) != 4 or tuple(input_shape[-3:]) != (3, 112, 112):
            raise RuntimeError("AdaFace ONNX 입력 shape은 (N, 3, 112, 112)여야 합니다.")
        if len(output_shape) != 2 or output_shape[-1] != 512:
            raise RuntimeError("AdaFace ONNX embedding 출력은 (N, 512)여야 합니다.")
        if input_value.type != "tensor(float)" or output_value.type != "tensor(float)":
            raise RuntimeError("AdaFace ONNX 입출력 dtype은 float32여야 합니다.")

    def prepare(self, ctx_id: int) -> None:
        del ctx_id

    def get_feat(self, aligned_bgr: np.ndarray) -> np.ndarray:
        batched = preprocess_aligned_face(aligned_bgr)
        (embedding,) = self._session.run(
            [self._embedding_output_name], {self._input_name: batched}
        )
        return np.asarray(embedding, dtype=np.float32)
