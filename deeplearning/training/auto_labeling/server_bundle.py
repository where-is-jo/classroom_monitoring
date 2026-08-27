from __future__ import annotations

import argparse
import json
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath

import yaml

from .core import SAFE_ID_PATTERN, sha256_bytes, sha256_file, write_json
from .errors import AutoLabelingError

TRAINING_ROOT = Path(__file__).resolve().parent.parent
REPO_PREFIX = PurePosixPath(
    "classroom-training/repo/classroom_monitoring/deeplearning/training"
)


def create_server_transfer_bundle(
    config_path: Path,
    dataset_archive: Path,
    base_model: Path,
    output_dir: Path,
    *,
    bundle_id: str,
) -> Path:
    """승인된 학습 데이터와 실행 코드의 GPU 서버 전송 목록을 만든다."""

    if SAFE_ID_PATTERN.fullmatch(bundle_id) is None:
        raise AutoLabelingError("bundle_id 형식이 올바르지 않습니다.")
    config_source = _required_file(config_path, suffixes={".yml", ".yaml"})
    dataset_source = _required_file(dataset_archive, suffixes={".zip"})
    model_source = _required_file(base_model, suffixes={".pt"})
    config = _load_config(config_source)
    server_root = _configured_server_root(config)
    dataset_sha256 = _verified_config_artifact(
        config,
        path_key="dataset_archive",
        hash_key="archive_sha256",
        local_path=dataset_source,
        expected_name=dataset_source.name,
        server_root=server_root,
    )
    model_sha256 = _verified_config_artifact(
        config,
        path_key="base_model",
        hash_key="base_model_sha256",
        local_path=model_source,
        expected_name=model_source.name,
        server_root=server_root,
    )
    privacy = _inspect_training_archive(dataset_source)
    original_frames_included = bool(privacy["original_frames_included"])
    entries = _runtime_source_entries(config_source, bundle_id=bundle_id)

    target_dir = output_dir.resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    archive_path = target_dir / f"classroom-training-code-{bundle_id}.zip"
    _write_code_archive(archive_path, entries)
    archive_sha256 = sha256_file(archive_path)
    receipt_path = target_dir / f"server-transfer-{bundle_id}.json"
    write_json(
        receipt_path,
        {
            "schema_version": 1,
            "bundle_id": bundle_id,
            "privacy_boundary": {
                "raw_video_included": False,
                "review_frames_included": False,
                "deidentified_dataset_only": not original_frames_included,
                "approved_original_frames_included": original_frames_included,
                "dataset_training_compatible": privacy["training_compatible"],
                "preprocessing_contract": privacy["preprocessing_contract"],
            },
            "code_archive": {
                "file_name": archive_path.name,
                "sha256": archive_sha256,
                "member_count": len(entries),
                "extract_under": str(server_root.parent),
            },
            "transfer_items": [
                {
                    "role": "code",
                    "local_file_name": archive_path.name,
                    "server_path": str(server_root.parent / archive_path.name),
                    "sha256": archive_sha256,
                },
                {
                    "role": (
                        "approved-original-frame-dataset"
                        if original_frames_included
                        else "deidentified-dataset"
                    ),
                    "local_file_name": dataset_source.name,
                    "server_path": str(config["dataset_archive"]),
                    "sha256": dataset_sha256,
                },
                {
                    "role": "base-model",
                    "local_file_name": model_source.name,
                    "server_path": str(config["base_model"]),
                    "sha256": model_sha256,
                },
            ],
            "embedded_files": [
                {"path": name, "sha256": sha256_bytes(content)}
                for name, content in entries
            ],
        },
    )
    return receipt_path


def _required_file(path: Path, *, suffixes: set[str]) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise AutoLabelingError("서버 전송 입력 파일을 찾을 수 없습니다.") from exc
    if not resolved.is_file() or resolved.is_symlink():
        raise AutoLabelingError("서버 전송 입력은 일반 파일이어야 합니다.")
    if resolved.suffix.lower() not in suffixes:
        raise AutoLabelingError("서버 전송 입력 파일 확장자가 올바르지 않습니다.")
    return resolved


def _load_config(path: Path) -> dict[str, object]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise AutoLabelingError("GPU 서버 학습 설정을 읽을 수 없습니다.") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise AutoLabelingError("GPU 서버 학습 설정 schema_version은 1이어야 합니다.")
    return value


def _configured_server_root(config: dict[str, object]) -> PurePosixPath:
    configured_root = config.get("server_root")
    if not isinstance(configured_root, str):
        raise AutoLabelingError("server_root 서버 절대 경로가 필요합니다.")
    server_root = PurePosixPath(configured_root)
    if (
        not server_root.is_absolute()
        or server_root.name != "classroom-training"
        or len(server_root.parts) < 3
        or ".." in server_root.parts
    ):
        raise AutoLabelingError(
            "server_root는 classroom-training으로 끝나는 승인된 절대 경로여야 합니다."
        )
    return server_root


def _verified_config_artifact(
    config: dict[str, object],
    *,
    path_key: str,
    hash_key: str,
    local_path: Path,
    expected_name: str,
    server_root: PurePosixPath,
) -> str:
    configured_path = config.get(path_key)
    if not isinstance(configured_path, str):
        raise AutoLabelingError(f"{path_key} 서버 절대 경로가 필요합니다.")
    server_path = PurePosixPath(configured_path)
    if (
        not server_path.is_absolute()
        or server_path.name != expected_name
        or not server_path.is_relative_to(server_root)
        or ".." in server_path.parts
    ):
        raise AutoLabelingError(
            f"{path_key}는 {server_root} 아래의 승인된 절대 경로여야 합니다."
        )
    expected_hash = config.get(hash_key)
    if (
        not isinstance(expected_hash, str)
        or re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash) is None
    ):
        raise AutoLabelingError(f"{hash_key}는 64자리 SHA-256이어야 합니다.")
    actual = sha256_file(local_path)
    if actual != expected_hash.lower():
        raise AutoLabelingError(f"{path_key}의 SHA-256이 설정과 다릅니다.")
    return actual


def _inspect_training_archive(path: Path) -> dict[str, object]:
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            roots: set[str] = set()
            names: set[str] = set()
            for info in infos:
                member = _validated_zip_member(info)
                roots.add(member.parts[0])
                names.add(info.filename.rstrip("/"))
            if len(roots) != 1:
                raise AutoLabelingError(
                    "비식별 학습 ZIP에는 최상위 폴더가 하나여야 합니다."
                )
            root_name = next(iter(roots))
            required = {
                f"{root_name}/data.yaml",
                f"{root_name}/manifest.json",
                f"{root_name}/privacy_receipt.json",
            }
            if not required.issubset(names):
                raise AutoLabelingError("비식별 학습 ZIP 필수 파일이 없습니다.")
            with archive.open(f"{root_name}/privacy_receipt.json") as source:
                privacy = json.load(source)
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise AutoLabelingError("비식별 학습 ZIP을 검증할 수 없습니다.") from exc
    if not isinstance(privacy, dict):
        raise AutoLabelingError("privacy_receipt.json 형식이 올바르지 않습니다.")
    if (
        privacy.get("training_compatible") is not True
        or privacy.get("raw_video_included") is not False
        or privacy.get("audio_included") is not False
        or privacy.get("absolute_source_paths_included") is not False
    ):
        raise AutoLabelingError(
            "GPU 서버에는 검증된 비식별 학습 ZIP만 보낼 수 있습니다."
        )
    contract = privacy.get("preprocessing_contract")
    if not isinstance(contract, dict):
        raise AutoLabelingError("비식별 전처리 계약이 없습니다.")
    original_frames_included = contract.get("method") == "original-frame-v1"
    if original_frames_included and (
        privacy.get("original_frames_included") is not True
        or privacy.get("approval_mode") != "approved-student-cohort-policy"
    ):
        raise AutoLabelingError(
            "GPU 서버 원본 프레임 반출에는 승인 정책과 명시적 영수증이 필요합니다."
        )
    return {
        "training_compatible": True,
        "preprocessing_contract": contract,
        "original_frames_included": original_frames_included,
    }


def _runtime_source_entries(
    config_source: Path, *, bundle_id: str
) -> list[tuple[str, bytes]]:
    entries: list[tuple[str, bytes]] = []
    package_root = TRAINING_ROOT / "auto_labeling"
    for source in sorted(package_root.rglob("*.py"), key=lambda item: item.as_posix()):
        relative = source.relative_to(TRAINING_ROOT)
        if "tests" in relative.parts or "__pycache__" in relative.parts:
            continue
        if source.is_symlink() or not source.is_file():
            raise AutoLabelingError("서버 코드 묶음에는 링크를 사용할 수 없습니다.")
        entries.append(
            (
                (REPO_PREFIX / PurePosixPath(relative.as_posix())).as_posix(),
                source.read_bytes(),
            )
        )
    settings = package_root / "config" / "settings.yml"
    entries.append(
        (
            (REPO_PREFIX / "auto_labeling/config/settings.yml").as_posix(),
            settings.read_bytes(),
        )
    )
    for name in ("requirements-server.txt", "pyproject.toml", "README.md"):
        source = TRAINING_ROOT / name
        entries.append(((REPO_PREFIX / name).as_posix(), source.read_bytes()))
    entries.append(
        (
            f"classroom-training/config/training-{bundle_id}.yml",
            config_source.read_bytes(),
        )
    )
    names = [name for name, _content in entries]
    if len(names) != len(set(names)):
        raise AutoLabelingError("서버 코드 묶음 경로가 중복됐습니다.")
    return sorted(entries, key=lambda item: item[0])


def _write_code_archive(path: Path, entries: list[tuple[str, bytes]]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists():
        raise AutoLabelingError("이전 서버 코드 묶음 임시 파일이 남아 있습니다.")
    try:
        with zipfile.ZipFile(
            temporary,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            for name, content in entries:
                info = zipfile.ZipInfo(name)
                info.date_time = (1980, 1, 1, 0, 0, 0)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, content, compresslevel=9)
        if path.exists():
            if sha256_file(path) != sha256_file(temporary):
                raise AutoLabelingError(
                    "같은 이름의 다른 서버 코드 묶음이 이미 있습니다."
                )
            temporary.unlink()
        else:
            temporary.replace(path)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise


def _validated_zip_member(info: zipfile.ZipInfo) -> PurePosixPath:
    if "\\" in info.filename or "\x00" in info.filename:
        raise AutoLabelingError("비식별 학습 ZIP에 안전하지 않은 경로가 있습니다.")
    member = PurePosixPath(info.filename)
    if (
        member.is_absolute()
        or not member.parts
        or ".." in member.parts
        or any(":" in part for part in member.parts)
    ):
        raise AutoLabelingError("비식별 학습 ZIP에 안전하지 않은 경로가 있습니다.")
    file_type = (info.external_attr >> 16) & 0o170000
    if file_type == stat.S_IFLNK:
        raise AutoLabelingError("비식별 학습 ZIP에는 링크를 사용할 수 없습니다.")
    return member


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="비식별 데이터와 코드만 GPU 서버로 보낼 전송 목록을 만듭니다."
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--dataset-archive", type=Path, required=True)
    parser.add_argument("--base-model", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--bundle-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        receipt = create_server_transfer_bundle(
            args.config,
            args.dataset_archive,
            args.base_model,
            args.output_dir,
            bundle_id=args.bundle_id,
        )
    except (AutoLabelingError, OSError) as exc:
        print(f"오류: {exc}")
        return 2
    print(
        json.dumps(
            {"status": "server-transfer-ready", "receipt": str(receipt)},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
