from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from auto_labeling import experiments
from auto_labeling.core import read_json, sha256_file
from auto_labeling.errors import AutoLabelingError
from auto_labeling.experiments import (
    TrainingConfig,
    compare_metric_files,
    train_person_detector,
)


class _FakeYolo:
    loaded_sources: list[str] = []
    train_calls: list[dict[str, Any]] = []
    training_data_configs: list[dict[str, Any]] = []

    def __init__(self, source: str) -> None:
        self.source = source
        source_path = Path(source)
        self.save_dir = (
            source_path.parents[1] if len(source_path.parents) > 1 else Path.cwd()
        )
        self.loaded_sources.append(source)

    def train(self, **kwargs: Any) -> SimpleNamespace:
        self.train_calls.append(kwargs)
        if "data" in kwargs:
            self.training_data_configs.append(
                experiments.yaml.safe_load(
                    Path(kwargs["data"]).read_text(encoding="utf-8")
                )
            )
            self.save_dir = Path(kwargs["project"]) / kwargs["name"]
        weights = self.save_dir / "weights"
        weights.mkdir(parents=True, exist_ok=True)
        (weights / "best.pt").write_bytes(b"resumed-best")
        (weights / "last.pt").write_bytes(b"resumed-last")
        return SimpleNamespace(save_dir=self.save_dir)


def test_training_uses_absolute_runtime_dataset_path_without_mutating_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    original_yaml = (
        "path: .\ntrain: images/train\nval: images/val\nnc: 1\nnames:\n  0: person\n"
    )
    data_yaml = dataset / "data.yaml"
    data_yaml.write_text(original_yaml, encoding="utf-8")
    output = tmp_path / "runs"

    _FakeYolo.loaded_sources.clear()
    _FakeYolo.train_calls.clear()
    _FakeYolo.training_data_configs.clear()
    monkeypatch.setattr(experiments, "_load_yolo", lambda: _FakeYolo)
    monkeypatch.setattr(
        experiments,
        "validate_privacy_export",
        lambda _path: {"status": "valid", "training_compatible": True},
    )

    receipt_path = train_person_detector(
        "yolo11n.pt",
        dataset,
        output,
        experiment_name="pilot-yolo11n-v005-seed42",
        config=TrainingConfig(epochs=1, device="cpu"),
    )

    assert data_yaml.read_text(encoding="utf-8") == original_yaml
    assert _FakeYolo.training_data_configs[0]["path"] == str(dataset.resolve())
    assert not list(output.glob(".training-data-*"))
    receipt = read_json(receipt_path)
    assert receipt["runtime_data_yaml_path_mode"] == "absolute-dataset-root"


def test_training_accepts_local_yolo11_model_and_records_source_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "data.yaml").write_text("path: .\n", encoding="utf-8")
    model = tmp_path / "models" / "yolo11n.pt"
    model.parent.mkdir()
    model.write_bytes(b"local-yolo11n")
    output = tmp_path / "runs"
    _FakeYolo.loaded_sources.clear()
    _FakeYolo.train_calls.clear()
    monkeypatch.setattr(experiments, "_load_yolo", lambda: _FakeYolo)
    monkeypatch.setattr(
        experiments,
        "validate_privacy_export",
        lambda _path: {"status": "valid", "training_compatible": True},
    )

    receipt_path = train_person_detector(
        str(model),
        dataset,
        output,
        experiment_name="server-yolo11n-v001-seed42",
        config=TrainingConfig(epochs=1, device="cpu"),
    )

    receipt = read_json(receipt_path)
    assert _FakeYolo.loaded_sources == [str(model)]
    assert receipt["model_file_name"] == "yolo11n.pt"
    assert receipt["source_model_sha256"] == sha256_file(model)


def test_training_isolates_ultralytics_user_config_under_output_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "data.yaml").write_text("path: .\n", encoding="utf-8")
    output = tmp_path / "runs"
    observed: dict[str, str | None] = {}

    def fake_load_yolo() -> type[_FakeYolo]:
        observed["config_dir"] = os.environ.get("YOLO_CONFIG_DIR")
        return _FakeYolo

    monkeypatch.setenv("YOLO_CONFIG_DIR", str(tmp_path / "user-config"))
    monkeypatch.setattr(experiments, "_load_yolo", fake_load_yolo)
    monkeypatch.setattr(
        experiments,
        "validate_privacy_export",
        lambda _path: {"status": "valid", "training_compatible": True},
    )

    receipt_path = train_person_detector(
        "yolo11n.pt",
        dataset,
        output,
        experiment_name="server-yolo11n-v001-seed42",
        config=TrainingConfig(epochs=1, device="cpu"),
    )

    expected = (output / ".ultralytics").resolve()
    assert observed["config_dir"] == str(expected)
    assert Path(os.environ["YOLO_CONFIG_DIR"]) == tmp_path / "user-config"
    assert expected.is_dir()
    assert read_json(receipt_path)["ultralytics_config_dir"] == str(expected)


def test_training_can_resume_only_from_its_last_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "data.yaml").write_text("path: .\n", encoding="utf-8")
    output = tmp_path / "runs"
    checkpoint = output / "f1-yolov8n-v001-seed42" / "weights" / "last.pt"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"interrupted-last")
    checkpoint_sha256 = sha256_file(checkpoint)

    _FakeYolo.loaded_sources.clear()
    _FakeYolo.train_calls.clear()
    monkeypatch.setattr(experiments, "_load_yolo", lambda: _FakeYolo)
    monkeypatch.setattr(
        experiments,
        "validate_privacy_export",
        lambda _path: {"status": "valid", "training_compatible": True},
    )

    receipt_path = train_person_detector(
        "yolov8n.pt",
        dataset,
        output,
        experiment_name="f1-yolov8n-v001-seed42",
        config=TrainingConfig(device="cpu"),
        resume=True,
    )

    receipt = read_json(receipt_path)
    assert _FakeYolo.loaded_sources == [str(checkpoint)]
    assert _FakeYolo.train_calls == [{"resume": True}]
    assert receipt["resumed"] is True
    assert receipt["resumed_from_sha256"] == checkpoint_sha256


def test_training_resume_rejects_completed_experiment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "data.yaml").write_text("path: .\n", encoding="utf-8")
    experiment = tmp_path / "runs" / "f1-yolov8n-v001-seed42"
    (experiment / "weights").mkdir(parents=True)
    (experiment / "weights" / "last.pt").write_bytes(b"last")
    (experiment / "training_receipt.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        experiments,
        "validate_privacy_export",
        lambda _path: {"status": "valid", "training_compatible": True},
    )

    with pytest.raises(AutoLabelingError, match="이미 완료된 실험"):
        train_person_detector(
            "yolov8n.pt",
            dataset,
            tmp_path / "runs",
            experiment_name="f1-yolov8n-v001-seed42",
            resume=True,
        )


def _write_metrics(path: Path, *, model_label: str, evaluation_hash: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "model_label": model_label,
                "evaluation_frozen_sha256": evaluation_hash,
                "confidence": 0.25,
                "match_iou": 0.5,
                "image_size": 640,
                "device": "cpu",
                "frame_count": 10,
                "precision": 0.8,
                "recall": 0.7,
                "f1": 0.746,
            }
        ),
        encoding="utf-8",
    )


def test_metric_comparison_requires_same_frozen_evaluation_set(
    tmp_path: Path,
) -> None:
    first = tmp_path / "B0" / "metrics.json"
    second = tmp_path / "F1" / "metrics.json"
    _write_metrics(first, model_label="B0-yolov8n", evaluation_hash="evaluation-a")
    _write_metrics(second, model_label="F1-yolov8n", evaluation_hash="evaluation-b")

    with pytest.raises(AutoLabelingError, match="동결 평가 세트"):
        compare_metric_files([first, second], tmp_path / "comparison.json")


def test_metric_comparison_records_fixed_conditions(tmp_path: Path) -> None:
    first = tmp_path / "B0" / "metrics.json"
    second = tmp_path / "F1" / "metrics.json"
    _write_metrics(first, model_label="B0-yolov8n", evaluation_hash="evaluation-a")
    _write_metrics(second, model_label="F1-yolov8n", evaluation_hash="evaluation-a")

    output = compare_metric_files([first, second], tmp_path / "comparison.json")
    comparison = read_json(output)

    assert comparison["evaluation_frozen_sha256"] == "evaluation-a"
    assert comparison["fixed_conditions"] == {
        "confidence": 0.25,
        "match_iou": 0.5,
        "image_size": 640,
        "device": "cpu",
        "frame_count": 10,
    }
