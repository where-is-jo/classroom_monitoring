from __future__ import annotations

import importlib
import json
import zipfile
from pathlib import Path
from typing import Any

from auto_labeling.core import read_json, sha256_file
from auto_labeling.server_bundle import create_server_transfer_bundle

yaml: Any = importlib.import_module("yaml")


def _write_dataset_archive(path: Path) -> None:
    privacy = {
        "training_compatible": True,
        "raw_video_included": False,
        "audio_included": False,
        "absolute_source_paths_included": False,
        "preprocessing_contract": {
            "method": "uniform-full-frame-pixelation-v1",
            "pixelation_block_size": 8,
            "inference_preprocessing_required": True,
        },
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("colab-export/data.yaml", "path: .\n")
        archive.writestr("colab-export/manifest.json", "{}")
        archive.writestr(
            "colab-export/privacy_receipt.json",
            json.dumps(privacy),
        )


def test_server_bundle_contains_runtime_code_only_and_is_deterministic(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "colab-export-v008.zip"
    _write_dataset_archive(dataset)
    model = tmp_path / "yolo11n.pt"
    model.write_bytes(b"model")
    config = tmp_path / "training.yml"
    config.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "server_root": "/home/training-operator/classroom-training",
                "dataset_archive": (
                    "/home/training-operator/classroom-training/input/"
                    "colab-export-v008.zip"
                ),
                "archive_sha256": sha256_file(dataset),
                "base_model": (
                    "/home/training-operator/classroom-training/input/yolo11n.pt"
                ),
                "base_model_sha256": sha256_file(model),
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    output = tmp_path / "transfer"

    receipt = create_server_transfer_bundle(
        config,
        dataset,
        model,
        output,
        bundle_id="v008",
    )
    first = read_json(receipt)
    same_receipt = create_server_transfer_bundle(
        config,
        dataset,
        model,
        output,
        bundle_id="v008",
    )
    second = read_json(same_receipt)

    archive_path = output / "classroom-training-code-v008.zip"
    assert first == second
    assert first["code_archive"]["extract_under"] == "/home/training-operator"
    assert first["transfer_items"][0]["server_path"] == (
        "/home/training-operator/classroom-training-code-v008.zip"
    )
    assert first["code_archive"]["sha256"] == sha256_file(archive_path)
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
    assert (
        "classroom-training/repo/classroom_monitoring/deeplearning/"
        "training/auto_labeling/pipeline.py"
    ) in names
    assert (
        "classroom-training/repo/classroom_monitoring/deeplearning/"
        "training/requirements-server.txt"
    ) in names
    assert "classroom-training/config/training-v008.yml" in names
    assert not any("/tests/" in name for name in names)
    assert not any(name.endswith((".pt", ".mp4", ".jpg")) for name in names)
