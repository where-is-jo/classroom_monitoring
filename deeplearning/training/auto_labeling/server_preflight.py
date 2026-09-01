from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

TRAINING_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_PACKAGES = {
    "numpy": ("numpy",),
    "opencv": ("opencv-python", "opencv-python-headless"),
    "pyyaml": ("PyYAML",),
    "torch": ("torch",),
    "ultralytics": ("ultralytics",),
}


def collect_server_preflight(config_path: Path) -> dict[str, object]:
    """고비용 학습 모듈을 import하지 않고 GPU 서버 준비 상태를 조사한다."""

    issues: list[str] = []
    warnings: list[str] = []
    packages = {
        label: _first_distribution_version(distributions)
        for label, distributions in REQUIRED_PACKAGES.items()
    }
    missing = [name for name, version in packages.items() if version is None]
    if missing:
        issues.append(f"필수 Python 패키지가 없습니다: {', '.join(missing)}")
    python_version = tuple(int(part) for part in platform.python_version_tuple()[:2])
    if python_version < (3, 12):
        issues.append("Python 3.12 이상이 필요합니다.")

    gpu_report = _nvidia_smi_report()
    config: dict[str, Any] | None = None
    try:
        config = _load_config(config_path)
    except (OSError, ValueError) as exc:
        issues.append(str(exc))

    dataset_report: dict[str, object] | None = None
    path_reports: dict[str, dict[str, object]] = {}
    model_report: dict[str, object] | None = None
    resolved_device: str | None = None
    if config is not None:
        try:
            dataset_report = _inspect_input(config)
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            issues.append(str(exc))
        for key in ("output_root", "extract_root"):
            if key == "extract_root" and _is_empty(config.get("dataset_archive")):
                continue
            try:
                report = _path_readiness(_path_value(config.get(key), key))
                path_reports[key] = report
                if report["writable"] is not True:
                    issues.append(f"{key}의 기존 상위 디렉터리에 쓰기 권한이 없습니다.")
            except ValueError as exc:
                issues.append(str(exc))
        try:
            model_report = _inspect_base_model(config)
            if model_report["source"] == "ultralytics-managed":
                warnings.append(
                    "서버가 오프라인이면 base_model 절대 경로와 SHA-256이 필요합니다."
                )
        except (OSError, ValueError) as exc:
            issues.append(str(exc))
        try:
            resolved_device = _resolve_device_from_smi(config, gpu_report)
            warnings.append(
                "GPU 선택은 현재 여유 메모리 검사이며 예약이 아닙니다. "
                "학습 직전에 팀과 사용 시간을 확인하세요."
            )
        except ValueError as exc:
            issues.append(str(exc))

    if shutil.which("nvcc") is None:
        warnings.append(
            "호스트에 nvcc가 없습니다. 사전 빌드 CUDA torch wheel을 쓰는 학습에는 "
            "필수가 아닙니다."
        )
    return {
        "schema_version": 1,
        "status": "ready-for-python-pipeline" if not issues else "not-ready",
        "artifact_writes_performed": False,
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "packages": packages,
        "environment_bootstrap": {
            "stdlib_venv_module": importlib.util.find_spec("venv") is not None,
            "stdlib_ensurepip_module": (
                importlib.util.find_spec("ensurepip") is not None
            ),
            "virtualenv": _first_distribution_version(("virtualenv",)),
        },
        "gpu": gpu_report,
        "requested_device": config.get("device") if config else None,
        "resolved_device": resolved_device,
        "allowed_cuda_devices": (
            config.get("allowed_cuda_devices") if config else None
        ),
        "dataset": dataset_report,
        "base_model": model_report,
        "paths": path_reports,
        "issues": issues,
        "warnings": warnings,
    }


def _load_config(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise ValueError("학습 설정 YAML을 읽으려면 PyYAML이 필요합니다.") from exc
    try:
        raw = yaml.safe_load(path.resolve(strict=True).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError("학습 설정 YAML을 읽을 수 없습니다.") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("학습 설정 schema_version은 1이어야 합니다.")
    return raw


def _inspect_input(config: dict[str, Any]) -> dict[str, object]:
    dataset_dir = config.get("dataset_dir")
    dataset_archive = config.get("dataset_archive")
    if _is_empty(dataset_dir) == _is_empty(dataset_archive):
        raise ValueError("dataset_dir과 dataset_archive 중 정확히 하나를 지정하세요.")
    if not _is_empty(dataset_dir):
        root = _path_value(dataset_dir, "dataset_dir").resolve(strict=True)
        missing = [
            name
            for name in ("data.yaml", "manifest.json", "privacy_receipt.json")
            if not (root / name).is_file()
        ]
        if missing:
            raise ValueError(f"데이터셋 필수 파일이 없습니다: {missing}")
        return {
            "source": "directory",
            "path": str(root),
            "full_privacy_validation_pending": True,
        }

    archive = _path_value(dataset_archive, "dataset_archive").resolve(strict=True)
    if archive.suffix.lower() != ".zip":
        raise ValueError("학습 데이터 압축 입력은 ZIP만 지원합니다.")
    expected_hash = config.get("archive_sha256")
    if not isinstance(expected_hash, str) or not _is_sha256(expected_hash):
        raise ValueError(
            "dataset_archive를 사용할 때 64자리 archive_sha256이 필요합니다."
        )
    actual_hash = _sha256_file(archive)
    if actual_hash != expected_hash.lower():
        raise ValueError("학습 데이터 압축 파일 SHA-256이 다릅니다.")
    with zipfile.ZipFile(archive) as source:
        infos = source.infolist()
        roots: set[str] = set()
        names: set[str] = set()
        for info in infos:
            member = _validated_zip_member(info)
            roots.add(member.parts[0])
            names.add(info.filename.rstrip("/"))
        if len(roots) != 1:
            raise ValueError("학습 ZIP에는 최상위 폴더가 하나여야 합니다.")
        root_name = next(iter(roots))
        required = {
            f"{root_name}/data.yaml",
            f"{root_name}/manifest.json",
            f"{root_name}/privacy_receipt.json",
        }
        missing = sorted(required - names)
        if missing:
            raise ValueError(f"학습 ZIP 필수 파일이 없습니다: {missing}")
        uncompressed_bytes = sum(info.file_size for info in infos)
    return {
        "source": "zip",
        "path": str(archive),
        "sha256": actual_hash,
        "archive_root": root_name,
        "member_count": len(infos),
        "uncompressed_bytes": uncompressed_bytes,
        "full_privacy_validation_pending": True,
    }


def _inspect_base_model(config: dict[str, Any]) -> dict[str, object]:
    reference = config.get("base_model", "yolo11n.pt")
    if not isinstance(reference, str) or Path(reference).name != "yolo11n.pt":
        raise ValueError("base_model은 yolo11n.pt여야 합니다.")
    if reference == "yolo11n.pt":
        if not _is_empty(config.get("base_model_sha256")):
            raise ValueError(
                "base_model_sha256을 쓰려면 base_model 절대 경로를 지정하세요."
            )
        return {
            "reference": reference,
            "source": "ultralytics-managed",
            "sha256": None,
        }
    path = _path_value(reference, "base_model").resolve(strict=True)
    expected_hash = config.get("base_model_sha256")
    if not isinstance(expected_hash, str) or not _is_sha256(expected_hash):
        raise ValueError("로컬 base_model에는 64자리 base_model_sha256이 필요합니다.")
    actual_hash = _sha256_file(path)
    if actual_hash != expected_hash.lower():
        raise ValueError("기준 모델 파일 SHA-256이 다릅니다.")
    return {
        "reference": str(path),
        "source": "local-file",
        "sha256": actual_hash,
    }


def _nvidia_smi_report() -> dict[str, object]:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return {"available": False, "devices": [], "error": "nvidia-smi-not-found"}
    try:
        result = subprocess.run(
            [
                executable,
                "--query-gpu=index,name,driver_version,memory.total,memory.free,compute_cap",
                "--format=csv,noheader,nounits",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"available": True, "devices": [], "error": "nvidia-smi-failed"}
    devices: list[dict[str, object]] = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 6:
            continue
        try:
            devices.append(
                {
                    "index": int(parts[0]),
                    "name": parts[1],
                    "driver_version": parts[2],
                    "total_mib": int(parts[3]),
                    "free_mib": int(parts[4]),
                    "compute_capability": parts[5],
                }
            )
        except ValueError:
            continue
    return {
        "available": True,
        "exit_code": result.returncode,
        "devices": devices,
        "error": None if result.returncode == 0 else "nvidia-smi-nonzero",
    }


def _resolve_device_from_smi(
    config: dict[str, Any], gpu_report: dict[str, object]
) -> str:
    if config.get("require_cuda", True) is not True:
        return str(config.get("device", "auto"))
    raw_devices = gpu_report.get("devices")
    if not isinstance(raw_devices, list) or not raw_devices:
        raise ValueError("CUDA GPU를 확인할 수 없습니다.")
    devices = {
        int(device["index"]): device
        for device in raw_devices
        if isinstance(device, dict) and isinstance(device.get("index"), int)
    }
    allowed_raw = config.get("allowed_cuda_devices")
    if not isinstance(allowed_raw, list) or not allowed_raw:
        raise ValueError(
            "공용 GPU 서버에서는 allowed_cuda_devices를 비어 있지 않은 정수 배열로 "
            "지정해야 합니다."
        )
    if any(
        isinstance(index, bool) or not isinstance(index, int) for index in allowed_raw
    ):
        raise ValueError("allowed_cuda_devices는 정수 배열이어야 합니다.")
    allowed = tuple(int(index) for index in allowed_raw)
    if len(allowed) != len(set(allowed)):
        raise ValueError("allowed_cuda_devices가 중복됐습니다.")
    missing = sorted(set(allowed) - set(devices))
    if missing:
        raise ValueError(f"허용 목록의 CUDA 장치를 찾을 수 없습니다: {missing}")
    requested = str(config.get("device", "auto")).strip().lower()
    if requested == "auto":
        selected = max(
            allowed,
            key=lambda index: (int(devices[index]["free_mib"]), -index),
        )
    else:
        normalized = requested.removeprefix("cuda:")
        if not normalized.isdigit():
            raise ValueError("서버 학습 device는 auto 또는 단일 CUDA 번호여야 합니다.")
        selected = int(normalized)
        if selected not in allowed:
            raise ValueError(f"승인되지 않은 CUDA 장치를 요청했습니다: {selected}")
    minimum_gib = config.get("minimum_cuda_free_gib", 8)
    if isinstance(minimum_gib, bool) or not isinstance(minimum_gib, (int, float)):
        raise ValueError("minimum_cuda_free_gib는 숫자여야 합니다.")
    free_mib = int(devices[selected]["free_mib"])
    if free_mib < float(minimum_gib) * 1024:
        raise ValueError(
            f"GPU {selected} 여유 메모리 {free_mib / 1024:.1f} GiB가 "
            f"최소 {float(minimum_gib):.1f} GiB보다 작습니다."
        )
    return str(selected)


def _path_value(value: object, key: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} 절대 경로가 필요합니다.")
    expanded = os.path.expandvars(os.path.expanduser(value.strip()))
    path = Path(expanded)
    if not path.is_absolute():
        raise ValueError(f"{key}는 절대 경로여야 합니다.")
    return path


def _path_readiness(path: Path) -> dict[str, object]:
    target = path.resolve()
    existing = target
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    writable = existing.is_dir() and os.access(existing, os.W_OK)
    free_bytes: int | None = None
    if existing.is_dir():
        try:
            free_bytes = shutil.disk_usage(existing).free
        except OSError:
            pass
    return {
        "path": str(target),
        "existing_ancestor": str(existing),
        "writable": writable,
        "free_bytes": free_bytes,
    }


def _validated_zip_member(info: zipfile.ZipInfo) -> PurePosixPath:
    if "\\" in info.filename or "\x00" in info.filename:
        raise ValueError("학습 ZIP에 안전하지 않은 경로가 있습니다.")
    path = PurePosixPath(info.filename)
    if (
        path.is_absolute()
        or not path.parts
        or ".." in path.parts
        or any(":" in part for part in path.parts)
    ):
        raise ValueError("학습 ZIP에 안전하지 않은 경로가 있습니다.")
    file_type = (info.external_attr >> 16) & 0o170000
    if file_type == stat.S_IFLNK:
        raise ValueError("학습 ZIP에는 심볼릭 링크를 사용할 수 없습니다.")
    return path


def _first_distribution_version(names: tuple[str, ...]) -> str | None:
    for name in names:
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            continue
    return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdefABCDEF" for character in value
    )


def _is_empty(value: object) -> bool:
    return value is None or value == ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="학습 패키지 import 전에 GPU 서버 준비 상태를 읽기 전용으로 검사합니다."
    )
    parser.add_argument("--config", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = collect_server_preflight(args.config)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ready-for-python-pipeline" else 2


if __name__ == "__main__":
    raise SystemExit(main())
