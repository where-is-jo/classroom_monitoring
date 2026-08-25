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

    result = verify_person_model_contract(str(model), str(contract), {0: "person"})

    assert result is not None
    assert result["preprocessing_contract"]["inference_preprocessing_required"] is False


def test_model_hash_mismatch_stops_startup(tmp_path: Path) -> None:
    model, contract = _write_contract(tmp_path)
    model.write_bytes(b"different-model")

    with pytest.raises(ValueError, match="SHA-256"):
        verify_person_model_contract(str(model), str(contract), {0: "person"})


def test_pixelated_model_without_adapter_stops_startup(tmp_path: Path) -> None:
    model, contract = _write_contract(tmp_path, preprocessing_required=True)

    with pytest.raises(ValueError, match="필수 추론 전처리 어댑터"):
        verify_person_model_contract(str(model), str(contract), {0: "person"})
