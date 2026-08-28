from __future__ import annotations

from pathlib import Path

import pytest

from deeplearning.training import prepare_adaface_model


def test_공식_모델_revision과_가중치_hash를_고정한다() -> None:
    assert len(prepare_adaface_model.MODEL_REVISION) == 40
    assert prepare_adaface_model.MODEL_REPOSITORY == (
        "minchul/cvlface_adaface_ir50_webface4m"
    )
    assert set(prepare_adaface_model.MODEL_FILE_SHA256) == {
        "pretrained_model/model.pt",
        "model.safetensors",
    }
    assert all(
        len(value) == 64 for value in prepare_adaface_model.MODEL_FILE_SHA256.values()
    )
    assert len(prepare_adaface_model.ONNX_FILE_SHA256) == 64


def test_가중치_hash가_다르면_변환_전에_거부한다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_path = tmp_path / "model.pt"
    model_path.write_bytes(b"tampered")
    monkeypatch.setattr(
        prepare_adaface_model,
        "MODEL_FILE_SHA256",
        {"model.pt": "0" * 64},
    )

    with pytest.raises(RuntimeError, match="SHA-256"):
        prepare_adaface_model.verify_model_files(tmp_path)


def test_ONNX_hash가_고정_산출물과_다르면_거부한다(tmp_path: Path) -> None:
    output_path = tmp_path / "adaface.onnx"
    output_path.write_bytes(b"tampered")

    with pytest.raises(RuntimeError, match="ONNX SHA-256"):
        prepare_adaface_model.verify_onnx(output_path)
