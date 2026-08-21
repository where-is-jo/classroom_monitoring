"""AdaFace ONNX 모델을 InsightFace recognizer와 같은 인터페이스로 감싼다.

`face_identity.py`와 `cross_camera_demo.build_face_engine()`은 recognizer가
`.prepare(ctx_id)`와 `.get_feat(aligned_bgr) -> (1, 512)`를 갖고 있다고
가정한다(InsightFace 관례). InsightFace의 `get_model()`은 ONNX 내부
metadata·출력 shape로 모델 종류를 자동판별하므로, InsightFace가 만들지 않은
AdaFace ONNX를 넣으면 잘못된 클래스로 판별될 위험이 있다 — 그래서 여기서는
`get_model()`을 쓰지 않고 onnxruntime을 직접 호출한다.

전처리는 공식 AdaFace `to_input()`과 동일하게 맞춘다: BGR, 112x112,
`(pixel/255 - 0.5) / 0.5`, NCHW. (참고: https://github.com/mk-minchul/AdaFace)
`prepare_adaface_model.py`로 만든 ONNX는 `[embedding, norm]` 두 출력을 갖는데,
여기서는 첫 번째 embedding 출력만 쓴다.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

ADAFACE_INPUT_SIZE = 112


def preprocess_aligned_face(aligned_bgr: np.ndarray) -> np.ndarray:
    """정렬된 112x112 BGR 얼굴을 AdaFace 입력 텐서 (1, 3, 112, 112)로 바꾼다."""
    if aligned_bgr.shape[:2] != (ADAFACE_INPUT_SIZE, ADAFACE_INPUT_SIZE):
        raise ValueError(
            f"AdaFace는 {ADAFACE_INPUT_SIZE}x{ADAFACE_INPUT_SIZE} 정렬 얼굴이 필요합니다: {aligned_bgr.shape}"
        )
    if aligned_bgr.ndim != 3 or aligned_bgr.shape[2] != 3:
        raise ValueError(f"AdaFace 입력은 3채널 BGR이어야 합니다: {aligned_bgr.shape}")
    normalized = (aligned_bgr.astype(np.float32) / 255.0 - 0.5) / 0.5
    return np.expand_dims(normalized.transpose(2, 0, 1), axis=0).astype(np.float32)


class AdaFaceOnnxRecognizer:
    """`recognizer.get_feat(aligned_bgr)` 하나로 InsightFace recognizer를 대체한다."""

    def __init__(self, model_path: Path, *, providers: list[str]) -> None:
        import onnxruntime as ort

        self._session = ort.InferenceSession(str(model_path), providers=providers)
        self._input_name = self._session.get_inputs()[0].name
        self._embedding_output_name = self._session.get_outputs()[0].name

    def prepare(self, ctx_id: int) -> None:
        """InsightFace recognizer 호출부와 시그니처를 맞추기 위한 자리.

        provider는 이미 `__init__`에서 세션 생성 시 고정되므로 여기서는 아무것도 하지 않는다.
        """
        del ctx_id

    def get_feat(self, aligned_bgr: np.ndarray) -> np.ndarray:
        batched = preprocess_aligned_face(aligned_bgr)
        (embedding,) = self._session.run([self._embedding_output_name], {self._input_name: batched})
        return embedding
