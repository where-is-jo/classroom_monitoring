from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from deeplearning.face_recognizer import (
    _validate_recognizer_output,
    load_face_recognizer_config,
)


def _model(tmp_path: Path) -> Path:
    path = tmp_path / "model.onnx"
    path.write_bytes(b"onnx-placeholder")
    return path


@pytest.mark.parametrize(
    ("kind", "model_version", "preprocessing", "collection"),
    [
        (
            "arcface",
            "insightface-buffalo_l-w600k_r50-v0.7",
            "insightface-norm-crop-112-v1",
            "face_embeddings_arcface",
        ),
        (
            "adaface",
            "cvlface-adaface-ir50-webface4m-fe7718c6",
            "cvlface-rgb-norm-crop-112-v1",
            "face_embeddings_adaface",
        ),
    ],
)
def test_모델_선택이_메타데이터와_컬렉션을_함께_고정한다(
    tmp_path: Path,
    kind: str,
    model_version: str,
    preprocessing: str,
    collection: str,
) -> None:
    config = load_face_recognizer_config(
        {
            "FACE_RECOGNIZER": kind,
            "FACE_RECOGNITION_MODEL_PATH": str(_model(tmp_path)),
        }
    )

    assert config.kind == kind
    assert config.metadata.model_name == kind
    assert config.metadata.model_version == model_version
    assert config.metadata.preprocessing_version == preprocessing
    assert config.collection_name == collection


def test_모델과_다른_컬렉션을_지정하면_기동_전에_거부한다(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="face_embeddings_adaface"):
        load_face_recognizer_config(
            {
                "FACE_RECOGNIZER": "adaface",
                "FACE_RECOGNITION_MODEL_PATH": str(_model(tmp_path)),
                "FACE_EMBEDDING_COLLECTION": "face_embeddings_arcface",
            }
        )


@pytest.mark.parametrize("kind", ["", "cosface", "AdaFace-v2"])
def test_지원하지_않는_모델명은_거부한다(tmp_path: Path, kind: str) -> None:
    with pytest.raises(RuntimeError, match="arcface 또는 adaface"):
        load_face_recognizer_config(
            {
                "FACE_RECOGNIZER": kind,
                "FACE_RECOGNITION_MODEL_PATH": str(_model(tmp_path)),
            }
        )


def test_없는_모델_경로는_거부한다(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="찾을 수 없습니다"):
        load_face_recognizer_config(
            {
                "FACE_RECOGNIZER": "adaface",
                "FACE_RECOGNITION_MODEL_PATH": str(tmp_path / "missing.onnx"),
            }
        )


def test_시험_추론_출력이_512차원이_아니면_거부한다() -> None:
    class InvalidRecognizer:
        def get_feat(self, image: np.ndarray) -> np.ndarray:
            assert image.shape == (112, 112, 3)
            return np.ones((1, 256), dtype=np.float32)

    with pytest.raises(RuntimeError, match="512차원"):
        _validate_recognizer_output(InvalidRecognizer())
