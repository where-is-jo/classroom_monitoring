from __future__ import annotations

import importlib
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from auto_labeling import server_preflight
from auto_labeling.server_preflight import collect_server_preflight

yaml: Any = importlib.import_module("yaml")


def _gpu_report() -> dict[str, object]:
    return {
        "available": True,
        "exit_code": 0,
        "error": None,
        "devices": [
            {
                "index": 0,
                "name": "NVIDIA L40S",
                "driver_version": "595.84",
                "total_mib": 46068,
                "free_mib": 4000,
                "compute_capability": "8.9",
            },
            {
                "index": 1,
                "name": "NVIDIA L40S",
                "driver_version": "595.84",
                "total_mib": 46068,
                "free_mib": 37000,
                "compute_capability": "8.9",
            },
            {
                "index": 2,
                "name": "NVIDIA L40S",
                "driver_version": "595.84",
                "total_mib": 46068,
                "free_mib": 45000,
                "compute_capability": "8.9",
            },
        ],
    }


def _install_ready_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server_preflight, "_nvidia_smi_report", _gpu_report)
    monkeypatch.setattr(
        server_preflight,
        "_first_distribution_version",
        lambda _names: "test-version",
    )


def test_bootstrap_preflight_honors_project_gpu_allowlist_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_ready_runtime(monkeypatch)
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    for name in ("data.yaml", "manifest.json", "privacy_receipt.json"):
        (dataset / name).write_text("{}", encoding="utf-8")
    output = tmp_path / "runs" / "person-detection"
    config_path = tmp_path / "training.yml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "dataset_dir": str(dataset),
                "dataset_archive": None,
                "output_root": str(output),
                "extract_root": str(tmp_path / "extract"),
                "experiment_name": "server-yolo11n-v001-seed42",
                "base_model": "yolo11n.pt",
                "device": "auto",
                "require_cuda": True,
                "minimum_cuda_free_gib": 8,
                "allowed_cuda_devices": [1],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    report = collect_server_preflight(config_path)

    assert report["status"] == "ready-for-python-pipeline"
    assert report["resolved_device"] == "1"
    assert report["artifact_writes_performed"] is False
    assert not output.exists()


def test_bootstrap_preflight_checks_zip_without_extracting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_ready_runtime(monkeypatch)
    archive = tmp_path / "dataset.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr("colab-export/data.yaml", "path: .\n")
        target.writestr("colab-export/manifest.json", "{}")
        target.writestr("colab-export/privacy_receipt.json", "{}")
        target.writestr("colab-export/images/train/frame.jpg", b"image")
    digest = server_preflight._sha256_file(archive)
    extract_root = tmp_path / "extract"
    config_path = tmp_path / "training.yml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "dataset_dir": None,
                "dataset_archive": str(archive),
                "archive_sha256": digest,
                "extract_root": str(extract_root),
                "output_root": str(tmp_path / "runs"),
                "experiment_name": "server-yolo11n-v001-seed42",
                "base_model": "yolo11n.pt",
                "device": "auto",
                "require_cuda": True,
                "minimum_cuda_free_gib": 8,
                "allowed_cuda_devices": [1],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    report = collect_server_preflight(config_path)

    dataset_report = report["dataset"]
    assert isinstance(dataset_report, dict)
    assert dataset_report["source"] == "zip"
    assert dataset_report["sha256"] == digest
    assert not extract_root.exists()


def test_bootstrap_preflight_reports_missing_packages_and_disallowed_gpu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server_preflight, "_nvidia_smi_report", _gpu_report)
    monkeypatch.setattr(
        server_preflight,
        "_first_distribution_version",
        lambda _names: None,
    )
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    for name in ("data.yaml", "manifest.json", "privacy_receipt.json"):
        (dataset / name).write_text(json.dumps({}), encoding="utf-8")
    config_path = tmp_path / "training.yml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "dataset_dir": str(dataset),
                "dataset_archive": None,
                "extract_root": str(tmp_path / "extract"),
                "output_root": str(tmp_path / "runs"),
                "base_model": "yolo11n.pt",
                "device": "2",
                "require_cuda": True,
                "allowed_cuda_devices": [1],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    report = collect_server_preflight(config_path)

    assert report["status"] == "not-ready"
    issues = report["issues"]
    assert isinstance(issues, list)
    assert any("필수 Python 패키지" in issue for issue in issues)
    assert any("승인되지 않은 CUDA" in issue for issue in issues)


def test_bootstrap_preflight_rejects_relative_server_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_ready_runtime(monkeypatch)
    config_path = tmp_path / "training.yml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "dataset_dir": "relative/dataset",
                "dataset_archive": None,
                "extract_root": str(tmp_path / "extract"),
                "output_root": str(tmp_path / "runs"),
                "base_model": "yolo11n.pt",
                "device": "auto",
                "require_cuda": True,
                "minimum_cuda_free_gib": 8,
                "allowed_cuda_devices": [1],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    report = collect_server_preflight(config_path)

    assert report["status"] == "not-ready"
    issues = report["issues"]
    assert isinstance(issues, list)
    assert any("dataset_dir는 절대 경로" in issue for issue in issues)


@pytest.mark.parametrize(
    "unsafe_name",
    ("colab-export/..\\escape.txt", "C:/escape.txt"),
)
def test_bootstrap_preflight_rejects_cross_platform_zip_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe_name: str,
) -> None:
    _install_ready_runtime(monkeypatch)
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr("colab-export/data.yaml", "path: .\n")
        target.writestr("colab-export/manifest.json", "{}")
        target.writestr("colab-export/privacy_receipt.json", "{}")
        target.writestr(unsafe_name, "unsafe")
    config_path = tmp_path / "training.yml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "dataset_dir": None,
                "dataset_archive": str(archive),
                "archive_sha256": server_preflight._sha256_file(archive),
                "extract_root": str(tmp_path / "extract"),
                "output_root": str(tmp_path / "runs"),
                "base_model": "yolo11n.pt",
                "device": "auto",
                "require_cuda": True,
                "minimum_cuda_free_gib": 8,
                "allowed_cuda_devices": [1],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    report = collect_server_preflight(config_path)

    assert report["status"] == "not-ready"
    issues = report["issues"]
    assert isinstance(issues, list)
    assert any("안전하지 않은 경로" in issue for issue in issues)
