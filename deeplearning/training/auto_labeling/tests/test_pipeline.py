from __future__ import annotations

import json
import sys
import zipfile
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from auto_labeling import pipeline
from auto_labeling.core import sha256_file, write_json, write_jsonl
from auto_labeling.errors import AutoLabelingError
from auto_labeling.experiments import TrainingConfig
from auto_labeling.pipeline import (
    LocalPipelineConfig,
    TrainingPipelineConfig,
    advance_local_pipeline,
    check_training_readiness,
    create_dataset_archive,
    load_local_pipeline_config,
    load_training_pipeline_config,
    materialize_training_dataset,
    run_training_pipeline,
)


def test_load_local_config_uses_n1_contract_and_training_relative_paths(
    tmp_path: Path,
) -> None:
    video_dir = tmp_path / "raw"
    workspace_dir = tmp_path / "workflow"
    config_path = tmp_path / "local.yml"
    config_path.write_text(
        "\n".join(
            (
                "schema_version: 1",
                "pipeline_id: classroom-v009",
                f"video_dir: {video_dir.as_posix()}",
                f"workspace_dir: {workspace_dir.as_posix()}",
                "camera_id: camera-01",
            )
        ),
        encoding="utf-8",
    )

    config = load_local_pipeline_config(config_path)

    assert config.n1_model_sha256 == pipeline.N1_MODEL_SHA256
    assert (
        config.n1_model_path == pipeline.TRAINING_ROOT / pipeline.N1_MODEL_RELATIVE_PATH
    )
    assert config.pixelation_block_size == 8
    assert config.force_full_review is True


def test_local_pipeline_stops_at_explicit_assignment_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video_dir = tmp_path / "raw"
    workspace = tmp_path / "workflow"
    video_dir.mkdir()
    (video_dir / "clip.mp4").write_bytes(b"video")
    config = LocalPipelineConfig(
        pipeline_id="classroom-v009",
        video_dir=video_dir,
        workspace_dir=workspace,
        camera_id="camera-01",
    )

    def fake_scan(
        input_dir: Path,
        output_dir: Path,
        **kwargs: object,
    ) -> Path:
        output_dir.mkdir(parents=True)
        write_json(
            output_dir / "session_manifest.json",
            {
                "schema_version": 1,
                "input_root": str(input_dir.resolve()),
                "timezone": kwargs["timezone_name"],
                "camera_id": kwargs["camera_id"],
                "expected_clip_seconds": kwargs["expected_clip_seconds"],
                "session_gap_seconds": kwargs["session_gap_seconds"],
                "overlap_tolerance_seconds": kwargs["overlap_tolerance_seconds"],
            },
        )
        write_jsonl(
            output_dir / "video_inventory.jsonl",
            [{"relative_path": "clip.mp4"}],
        )
        (output_dir / "session_assignments.csv").write_text(
            "session_id,role\nsession-001,\n",
            encoding="utf-8",
        )
        (output_dir / "session_timeline.html").write_text(
            "<html></html>", encoding="utf-8"
        )
        return output_dir

    monkeypatch.setattr(pipeline, "scan_video_folder", fake_scan)

    state = advance_local_pipeline(config)

    assert state["status"] == "waiting-for-session-assignments"
    assert state["detail"] == {"incomplete_session_ids": ["session-001"]}
    assert (workspace / "pipeline-state.json").is_file()


def test_local_pipeline_uses_verified_n1_and_reaches_training_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video_dir = tmp_path / "raw"
    workspace = tmp_path / "workflow"
    scan_dir = workspace / "01-scan"
    review_dir = tmp_path / "run" / "review" / "review-main"
    dataset_dir = tmp_path / "dataset"
    export_dir = workspace / "06-colab-export"
    video_dir.mkdir()
    scan_dir.mkdir(parents=True)
    (scan_dir / "session_assignments.csv").write_text(
        "session_id,role\nsession-001,dataset\n", encoding="utf-8"
    )
    review_dir.mkdir(parents=True)
    write_json(review_dir / "review-batch.json", {"frame_ids": ["frame-001"]})
    write_json(review_dir / "review-completed.json", {"status": "complete"})
    dataset_dir.mkdir()
    prelabel_calls: list[dict[str, object]] = []
    review_calls: list[dict[str, object]] = []
    config = LocalPipelineConfig(
        pipeline_id="classroom-v009",
        video_dir=video_dir,
        workspace_dir=workspace,
        camera_id="camera-01",
    )

    monkeypatch.setattr(pipeline, "_verify_scan_contract", lambda *_args: None)
    monkeypatch.setattr(
        pipeline,
        "_incomplete_assignment_sessions",
        lambda _path: [],
    )
    monkeypatch.setattr(
        pipeline,
        "partition_sessions",
        lambda *_args, **_kwargs: (workspace / "02-partition").mkdir(),
    )
    monkeypatch.setattr(
        pipeline,
        "_verify_partition_contract",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        pipeline,
        "prepare_clean_source_run",
        lambda *_args, **_kwargs: review_dir.parent.parent,
    )

    def fake_prelabel(*_args: object, **kwargs: object) -> Path:
        prelabel_calls.append(kwargs)
        return review_dir.parent.parent / "candidate-labels"

    def fake_prepare_review(*_args: object, **kwargs: object) -> Path:
        review_calls.append(kwargs)
        return review_dir

    monkeypatch.setattr(pipeline, "run_prelabel", fake_prelabel)
    monkeypatch.setattr(pipeline, "prepare_review", fake_prepare_review)
    monkeypatch.setattr(pipeline, "verify_review_receipt", lambda _path: {})
    monkeypatch.setattr(
        pipeline, "publish_dataset", lambda *_args, **_kwargs: dataset_dir
    )
    monkeypatch.setattr(
        pipeline,
        "validate_dataset",
        lambda _path: {"frame_count": 25, "split_counts": {"train": 20, "val": 5}},
    )

    def fake_export(*_args: object, **_kwargs: object) -> Path:
        export_dir.mkdir()
        return export_dir

    monkeypatch.setattr(pipeline, "export_deidentified_dataset", fake_export)
    monkeypatch.setattr(
        pipeline,
        "validate_privacy_export",
        lambda _path: {
            "status": "valid",
            "training_compatible": True,
            "split_counts": {"train": 20, "val": 5},
        },
    )
    monkeypatch.setattr(pipeline, "_verify_export_source", lambda *_args: None)
    monkeypatch.setattr(
        pipeline,
        "create_dataset_archive",
        lambda _dataset, archive: archive.with_suffix(".zip.receipt.json"),
    )

    state = advance_local_pipeline(config)

    assert state["status"] == "ready-for-training"
    assert prelabel_calls == [
        {
            "device": "cpu",
            "expected_model_sha256": pipeline.N1_MODEL_SHA256,
            "input_preprocessing": {
                "schema_version": 1,
                "method": "uniform-full-frame-pixelation-v1",
                "label_derived": False,
                "training_compatible": True,
                "inference_preprocessing_required": True,
                "pixelation_block_size": 8,
            },
        }
    ]
    assert review_calls == [{"batch_id": "review-main", "force_full": True}]


def test_dataset_archive_is_deterministic_and_safely_materialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = tmp_path / "colab-export-v009"
    (dataset / "images" / "train").mkdir(parents=True)
    (dataset / "images" / "train" / "frame.jpg").write_bytes(b"image")
    write_json(dataset / "manifest.json", {"schema_version": 1})
    write_json(dataset / "privacy_receipt.json", {"schema_version": 2})
    (dataset / "data.yaml").write_text("path: .\n", encoding="utf-8")
    archive = tmp_path / "dataset.zip"
    monkeypatch.setattr(
        pipeline,
        "validate_privacy_export",
        lambda _path: {"status": "valid", "training_compatible": True},
    )

    receipt = create_dataset_archive(dataset, archive)
    first_hash = sha256_file(archive)
    same_receipt = create_dataset_archive(dataset, archive)
    config = TrainingPipelineConfig(
        dataset_archive=archive,
        archive_sha256=first_hash,
        extract_root=tmp_path / "extracted",
        output_root=tmp_path / "runs",
        experiment_name="n2-yolo11n-v009-seed42",
        device="cpu",
        require_cuda=False,
    )

    extracted = materialize_training_dataset(config)

    assert receipt == same_receipt
    assert sha256_file(archive) == first_hash
    assert extracted.name == dataset.name
    assert (extracted / "images" / "train" / "frame.jpg").read_bytes() == b"image"


def test_materialize_rejects_zip_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr("../escape.txt", "unsafe")
    config = TrainingPipelineConfig(
        dataset_archive=archive,
        archive_sha256=sha256_file(archive),
        extract_root=tmp_path / "extract",
        output_root=tmp_path / "runs",
        experiment_name="n2-yolo11n-v009-seed42",
        device="cpu",
        require_cuda=False,
    )

    with pytest.raises(AutoLabelingError, match="안전하지 않은 경로"):
        materialize_training_dataset(config)


@pytest.mark.parametrize(
    "unsafe_name",
    ("colab-export/..\\escape.txt", "C:/escape.txt"),
)
def test_materialize_rejects_cross_platform_zip_paths(
    tmp_path: Path, unsafe_name: str
) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr(unsafe_name, "unsafe")
    config = TrainingPipelineConfig(
        dataset_archive=archive,
        archive_sha256=sha256_file(archive),
        extract_root=tmp_path / "extract",
        output_root=tmp_path / "runs",
        experiment_name="n2-yolo11n-v009-seed42",
        device="cpu",
        require_cuda=False,
    )

    with pytest.raises(AutoLabelingError, match="안전하지 않은 경로"):
        materialize_training_dataset(config)


def test_training_pipeline_runs_smoke_then_full_and_bundles_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "data.yaml").write_text("path: .\n", encoding="utf-8")
    write_json(dataset / "manifest.json", {"schema_version": 1})
    write_json(dataset / "privacy_receipt.json", {"schema_version": 2})
    calls: list[str] = []

    monkeypatch.setattr(
        pipeline,
        "validate_privacy_export",
        lambda _path: {
            "status": "valid",
            "training_compatible": True,
            "split_counts": {"train": 20, "val": 5},
        },
    )

    def fake_train(
        model_name: str,
        dataset_dir: Path,
        output_root: Path,
        *,
        experiment_name: str,
        config: TrainingConfig,
        resume: bool,
    ) -> Path:
        assert resume is False
        calls.append(experiment_name)
        experiment = output_root / experiment_name
        (experiment / "weights").mkdir(parents=True)
        (experiment / "weights" / "best.pt").write_bytes(b"best")
        (experiment / "weights" / "last.pt").write_bytes(b"last")
        (experiment / "results.csv").write_text("epoch\n1\n", encoding="utf-8")
        (experiment / "results.png").write_bytes(b"png")
        (experiment / "args.yaml").write_text("model: yolo11n.pt\n", encoding="utf-8")
        receipt = experiment / "training_receipt.json"
        write_json(
            receipt,
            {
                "model_name": model_name,
                "experiment_name": experiment_name,
                "config": asdict(config),
                "data_yaml_sha256": sha256_file(dataset_dir / "data.yaml"),
                "best_weight_sha256": sha256_file(experiment / "weights" / "best.pt"),
                "last_weight_sha256": sha256_file(experiment / "weights" / "last.pt"),
            },
        )
        return receipt

    def fake_threshold(
        best_weight: Path,
        dataset_dir: Path,
        output_path: Path,
        **_kwargs: object,
    ) -> Path:
        write_json(
            output_path,
            {
                "model_sha256": sha256_file(best_weight),
                "dataset_manifest_sha256": sha256_file(dataset_dir / "manifest.json"),
                "best": {"confidence": 0.25},
            },
        )
        return output_path

    monkeypatch.setattr(pipeline, "train_person_detector", fake_train)
    monkeypatch.setattr(pipeline, "select_validation_f1_threshold", fake_threshold)
    config = TrainingPipelineConfig(
        dataset_dir=dataset,
        output_root=tmp_path / "runs",
        experiment_name="n2-yolo11n-v009-seed42",
        device="cpu",
        require_cuda=False,
    )

    result = run_training_pipeline(config)

    assert calls == [
        "smoke-n2-yolo11n-v009-seed42",
        "n2-yolo11n-v009-seed42",
    ]
    assert result["status"] == "training-complete"
    assert Path(str(result["best_weight"])).is_file()
    assert Path(str(result["result_bundle"])).is_file()


def test_training_config_accepts_verified_local_yolo11_model(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    model = tmp_path / "models" / "yolo11n.pt"
    model.parent.mkdir()
    model.write_bytes(b"official-yolo11n")
    config_path = tmp_path / "training.yml"
    config_path.write_text(
        "\n".join(
            (
                "schema_version: 1",
                f"dataset_dir: {dataset.as_posix()}",
                "dataset_archive: null",
                f"output_root: {(tmp_path / 'runs').as_posix()}",
                "experiment_name: server-yolo11n-v001-seed42",
                f"base_model: {model.as_posix()}",
                f"base_model_sha256: {sha256_file(model)}",
                "device: cpu",
                "require_cuda: false",
                "allowed_cuda_devices: [1]",
            )
        ),
        encoding="utf-8",
    )

    config = load_training_pipeline_config(config_path)

    assert config.base_model == str(model)
    assert config.base_model_sha256 == sha256_file(model)
    assert config.minimum_cuda_free_gib == 8.0
    assert config.allowed_cuda_devices == (1,)


def test_training_readiness_is_read_only_and_reports_gpu_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    output = tmp_path / "runs" / "person-detection"
    config = TrainingPipelineConfig(
        dataset_dir=dataset,
        output_root=output,
        experiment_name="server-yolo11n-v001-seed42",
        device="0",
        require_cuda=True,
    )
    monkeypatch.setattr(
        pipeline,
        "validate_privacy_export",
        lambda _path: {
            "status": "valid",
            "training_compatible": True,
            "split_counts": {"train": 20, "val": 5},
        },
    )
    monkeypatch.setattr(
        pipeline,
        "_training_runtime_report",
        lambda: {
            "python": "3.12.3",
            "packages": {
                "numpy": "2.0",
                "opencv": "4.10",
                "pyyaml": "6.0",
                "torch": "2.5",
                "ultralytics": "8.3",
            },
            "torch": {"cuda_available": True},
            "nvidia_smi": {"available": True},
        },
    )
    monkeypatch.setattr(
        pipeline, "_resolve_training_device", lambda *_args, **_kwargs: "0"
    )

    report = check_training_readiness(config)

    assert report["status"] == "ready-for-training"
    assert report["artifact_writes_performed"] is False
    assert report["resolved_device"] == "0"
    assert not output.exists()


def test_training_readiness_checks_zip_without_extracting_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "dataset.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr("colab-export/data.yaml", "path: .\n")
        target.writestr("colab-export/manifest.json", "{}")
        target.writestr("colab-export/privacy_receipt.json", "{}")
        target.writestr("colab-export/images/train/frame.jpg", b"image")
    extract_root = tmp_path / "extract"
    config = TrainingPipelineConfig(
        dataset_archive=archive,
        archive_sha256=sha256_file(archive),
        extract_root=extract_root,
        output_root=tmp_path / "runs",
        experiment_name="server-yolo11n-v001-seed42",
        device="cpu",
        require_cuda=False,
    )
    monkeypatch.setattr(
        pipeline,
        "_training_runtime_report",
        lambda: {
            "python": "3.12.3",
            "packages": {
                "numpy": "2.0",
                "opencv": "4.10",
                "pyyaml": "6.0",
                "torch": "2.5",
                "ultralytics": "8.3",
            },
            "torch": {"cuda_available": False},
            "nvidia_smi": {"available": False},
        },
    )
    monkeypatch.setattr(
        pipeline,
        "_resolve_training_device",
        lambda *_args, **_kwargs: "cpu",
    )

    report = check_training_readiness(config)

    assert report["status"] == "ready-for-training"
    dataset_report = report["dataset"]
    assert isinstance(dataset_report, dict)
    assert dataset_report["source"] == "zip"
    assert not extract_root.exists()


def test_explicit_cuda_device_is_rejected_when_torch_has_no_cuda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            cuda=SimpleNamespace(
                is_available=lambda: False,
                device_count=lambda: 0,
            )
        ),
    )

    with pytest.raises(AutoLabelingError, match="CUDA GPU"):
        pipeline._resolve_training_device("0", require_cuda=True)


def test_auto_device_selects_gpu_with_most_free_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gib = 1024**3
    free_memory = {0: 2 * gib, 1: 12 * gib, 2: 10 * gib}
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            cuda=SimpleNamespace(
                is_available=lambda: True,
                device_count=lambda: 3,
                mem_get_info=lambda index: (free_memory[index], 48 * gib),
            )
        ),
    )

    selected = pipeline._resolve_training_device(
        "auto",
        require_cuda=True,
        minimum_free_bytes=8 * gib,
        allowed_cuda_devices=(0, 2),
    )

    assert selected == "2"
    with pytest.raises(AutoLabelingError, match="여유 메모리"):
        pipeline._resolve_training_device(
            "0",
            require_cuda=True,
            minimum_free_bytes=8 * gib,
            allowed_cuda_devices=(0, 2),
        )
    with pytest.raises(AutoLabelingError, match="승인되지 않은"):
        pipeline._resolve_training_device(
            "1",
            require_cuda=True,
            allowed_cuda_devices=(0, 2),
        )


def test_config_rejects_unknown_keys(tmp_path: Path) -> None:
    config_path = tmp_path / "invalid.yml"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pipeline_id": "v009",
                "video_dir": "raw",
                "workspace_dir": "workflow",
                "surprise": True,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(AutoLabelingError, match="알 수 없는"):
        load_local_pipeline_config(config_path)
