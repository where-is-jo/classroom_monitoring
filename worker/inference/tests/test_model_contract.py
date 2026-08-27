from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ..model_contract import verify_person_model_contract


def _write_contract(
    tmp_path: Path,
    *,
    model_bytes: bytes = b"original-frame-model",
    preprocessing_required: bool = False,
) -> tuple[Path, Path]:
    model = tmp_path / "best.pt"
    model.write_bytes(model_bytes)
    contract = tmp_path / "model_contract.json"
    contract.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model_sha256": hashlib.sha256(model_bytes).hexdigest(),
                "target_class_ids": {"0": "person"},
                "image_size": 1280,
                "preprocessing_contract": {
                    "schema_version": 1,
                    "method": (
                        "uniform-full-frame-pixelation-v1"
                        if preprocessing_required
                        else "original-frame-v1"
                    ),
                    "label_derived": False,
                    "training_compatible": True,
                    "inference_preprocessing_required": preprocessing_required,
                },
            }
        ),
        encoding="utf-8",
    )
    return model, contract


def test_original_frame_model_contract_is_accepted(tmp_path: Path) -> None:
    model, contract = _write_contract(tmp_path)

    result = verify_person_model_contract(
        str(model), str(contract), {0: "person"}, 1280
    )

    assert result is not None
    assert result["preprocessing_contract"]["inference_preprocessing_required"] is False


def test_model_hash_mismatch_stops_startup(tmp_path: Path) -> None:
    model, contract = _write_contract(tmp_path)
    model.write_bytes(b"different-model")

    with pytest.raises(ValueError, match="SHA-256"):
        verify_person_model_contract(str(model), str(contract), {0: "person"}, 1280)


def test_pixelated_model_without_adapter_stops_startup(tmp_path: Path) -> None:
    model, contract = _write_contract(tmp_path, preprocessing_required=True)

    with pytest.raises(ValueError, match="필수 추론 전처리 어댑터"):
        verify_person_model_contract(str(model), str(contract), {0: "person"}, 1280)


def test_image_size_mismatch_stops_startup(tmp_path: Path) -> None:
    model, contract = _write_contract(tmp_path)

    with pytest.raises(ValueError, match="INFERENCE_IMAGE_SIZE"):
        verify_person_model_contract(str(model), str(contract), {0: "person"}, 640)


def test_blank_contract_path_is_treated_as_local_opt_out() -> None:
    assert verify_person_model_contract("missing.pt", "", {0: "person"}, 1280) is None
