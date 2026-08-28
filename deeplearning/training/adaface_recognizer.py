"""학습·평가 코드가 운영 런타임과 같은 AdaFace 어댑터를 사용하게 한다."""

from __future__ import annotations

from deeplearning.adaface_recognizer import (
    ADAFACE_INPUT_SIZE,
    AdaFaceOnnxRecognizer,
    preprocess_aligned_face,
)

__all__ = [
    "ADAFACE_INPUT_SIZE",
    "AdaFaceOnnxRecognizer",
    "preprocess_aligned_face",
]
