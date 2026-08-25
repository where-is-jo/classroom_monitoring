from __future__ import annotations

import csv
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .core import (
    SAFE_ID_PATTERN,
    read_json,
    read_jsonl,
    sha256_bytes,
    sha256_file,
    write_json,
)
from .errors import AutoLabelingError

ROLES = {"dataset", "benchmark", "acceptance", "excluded"}
SPLITS = {"train", "val"}
SUBJECT_CATEGORIES = {"synthetic", "consenting-adult", "student"}


def partition_sessions(
    scan_dir: Path,
    assignments_path: Path,
    output_dir: Path,
    *,
    allow_approved_student_data: bool = False,
    require_approval_metadata: bool = True,
) -> Path:
    """검토 완료된 세션 배정을 불변 dataset/evaluation manifest로 변환한다."""

    scan_root = scan_dir.resolve(strict=True)
    if not scan_root.is_dir():
        raise AutoLabelingError("scan_dir은 디렉터리여야 합니다.")
    target = output_dir.resolve()
    if target.exists():
        raise AutoLabelingError("분할 출력 디렉터리가 이미 있습니다.")
    session_manifest_path = scan_root / "session_manifest.json"
    inventory_path = scan_root / "video_inventory.jsonl"
    session_manifest = read_json(session_manifest_path)
    if (
        not isinstance(session_manifest, dict)
        or session_manifest.get("schema_version") != 1
    ):
        raise AutoLabelingError("지원하지 않는 session manifest입니다.")
    raw_sessions = session_manifest.get("sessions")
    if not isinstance(raw_sessions, list) or not raw_sessions:
        raise AutoLabelingError("session manifest에 세션이 없습니다.")
    inventory = read_jsonl(inventory_path)
    assignments = _read_assignments(assignments_path)
    session_by_id = {
        _required_text(item.get("session_id"), "session_id"): item
        for item in raw_sessions
        if isinstance(item, dict)
    }
    if set(assignments) != set(session_by_id):
        missing = sorted(set(session_by_id) - set(assignments))
        unknown = sorted(set(assignments) - set(session_by_id))
        detail = missing[0] if missing else unknown[0]
        raise AutoLabelingError(f"세션 배정 목록이 scan 결과와 다릅니다: {detail}")

    dataset_session_ids = [
        session_id
        for session_id, assignment in assignments.items()
        if assignment["role"] == "dataset"
    ]
    session_splits = _assign_train_val(dataset_session_ids, assignments)
    sources_by_session = _inventory_by_session(inventory)
    dataset_sources: list[dict[str, Any]] = []
    evaluation_sources: list[dict[str, Any]] = []
    role_by_sha256: dict[str, str] = {}
    student_sessions: list[str] = []

    for session_id in sorted(session_by_id):
        assignment = assignments[session_id]
        role = assignment["role"]
        if role == "excluded":
            continue
        if require_approval_metadata:
            _validate_approval(assignment, session_id, allow_approved_student_data)
        if assignment["subject_category"] == "student":
            student_sessions.append(session_id)
        records = sources_by_session.get(session_id, [])
        if not records:
            raise AutoLabelingError(f"session_id={session_id}: 채택된 영상이 없습니다.")
        for record in records:
            digest = _required_sha256(record.get("sha256"), "sha256")
            previous_role = role_by_sha256.setdefault(digest, role)
            if previous_role != role:
                raise AutoLabelingError(
                    "동일 영상 해시가 서로 다른 역할에 배정됐습니다."
                )
            common = {
                "source_id": _required_text(record.get("source_id"), "source_id"),
                "file_path": _required_text(
                    record.get("absolute_path"), "absolute_path"
                ),
                "source_sha256": digest,
                "approval_reference": assignment["approval_reference"],
                "consent_scope": assignment["consent_scope"],
                "retention_expires_at": assignment["retention_expires_at"],
                "camera_id": _required_text(record.get("camera_id"), "camera_id"),
                "session_id": session_id,
                "captured_at": _required_text(record.get("captured_at"), "captured_at"),
                "subject_category": assignment["subject_category"],
            }
            if role == "dataset":
                dataset_sources.append(
                    {
                        **common,
                        "usage": "dataset",
                        "requested_split": session_splits[session_id],
                    }
                )
            else:
                scope = "benchmark" if role == "benchmark" else "acceptance"
                declared_scope = assignment["evaluation_scope"]
                if declared_scope and declared_scope != scope:
                    raise AutoLabelingError(
                        f"session_id={session_id}: role과 evaluation_scope가 다릅니다."
                    )
                evaluation_sources.append(
                    {
                        **common,
                        "usage": "evaluation",
                        "evaluation_scope": scope,
                    }
                )

    if not dataset_sources:
        raise AutoLabelingError("dataset 역할 세션이 한 개 이상 필요합니다.")
    if not evaluation_sources:
        raise AutoLabelingError(
            "benchmark 또는 acceptance 세션이 한 개 이상 필요합니다."
        )
    assignment_sha256 = sha256_file(assignments_path.resolve(strict=True))
    scan_fingerprint = _required_sha256(
        session_manifest.get("scan_fingerprint"), "scan_fingerprint"
    )
    run_suffix = sha256_bytes(f"{scan_fingerprint}:{assignment_sha256}".encode())[:12]
    run_id = f"person-dataset-{run_suffix}"
    split_receipt = {
        "schema_version": 1,
        "scan_fingerprint": scan_fingerprint,
        "assignments_sha256": assignment_sha256,
        "dataset_run_id": run_id,
        "policy": "session-hash-train90-val10-v1",
        "session_splits": session_splits,
        "role_counts": {
            role: sum(item["role"] == role for item in assignments.values())
            for role in sorted(ROLES)
        },
        "student_sessions": sorted(student_sessions),
        "approved_student_flag_used": bool(student_sessions),
        "approval_metadata_required": require_approval_metadata,
    }
    leak_check = {
        "schema_version": 1,
        "passed": True,
        "checks": {
            "unique_session_role": True,
            "unique_source_hash_role": True,
            "dataset_evaluation_disjoint": True,
            "session_split_disjoint": True,
            "frame_near_duplicate_check": "required-after-frame-extraction",
        },
        "dataset_source_count": len(dataset_sources),
        "evaluation_source_count": len(evaluation_sources),
    }

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{target.name}-", dir=target.parent
    ) as temp:
        temporary = Path(temp)
        write_json(
            temporary / "dataset_manifest.json",
            {
                "schema_version": 2,
                "manifest_role": "dataset",
                "run_id": run_id,
                "scan_fingerprint": scan_fingerprint,
                "sources": dataset_sources,
            },
        )
        write_json(
            temporary / "evaluation_manifest.json",
            {
                "schema_version": 1,
                "manifest_role": "evaluation",
                "evaluation_id": f"person-evaluation-{run_suffix}",
                "scan_fingerprint": scan_fingerprint,
                "sources": evaluation_sources,
            },
        )
        write_json(temporary / "split_receipt.json", split_receipt)
        write_json(temporary / "leak_check.json", leak_check)
        write_json(
            temporary / "privacy_export_required.json",
            {
                "schema_version": 1,
                "colab_export_allowed": False,
                "reason": "로컬 비식별화 export와 privacy_receipt 검증 후에만 허용",
                "student_data_present": bool(student_sessions),
                "approval_metadata_required": require_approval_metadata,
                "raw_video_export_allowed": False,
            },
        )
        temporary.replace(target)
    return target


def partition_validation_extension(
    scan_dir: Path,
    assignments_path: Path,
    output_dir: Path,
    base_export_dir: Path,
    *,
    allow_approved_student_data: bool = False,
) -> Path:
    """기존 비식별 train export에 추가할 val 전용 manifest를 만든다."""

    from .privacy import validate_privacy_export

    scan_root = scan_dir.resolve(strict=True)
    if not scan_root.is_dir():
        raise AutoLabelingError("scan_dir은 디렉터리여야 합니다.")
    target = output_dir.resolve()
    if target.exists():
        raise AutoLabelingError("분할 출력 디렉터리가 이미 있습니다.")

    base_root = base_export_dir.resolve(strict=True)
    base_report = validate_privacy_export(base_root)
    base_manifest_path = base_root / "manifest.json"
    base_manifest = read_json(base_manifest_path)
    if not isinstance(base_manifest, dict):
        raise AutoLabelingError("기준 Colab export manifest가 올바르지 않습니다.")
    base_items = base_manifest.get("items")
    if not isinstance(base_items, list) or not base_items:
        raise AutoLabelingError("기준 Colab export에 학습 항목이 없습니다.")
    base_train_count = sum(
        isinstance(item, dict) and item.get("split") == "train" for item in base_items
    )
    base_val_count = sum(
        isinstance(item, dict) and item.get("split") == "val" for item in base_items
    )
    if base_train_count < 1:
        raise AutoLabelingError("기준 Colab export에 train 항목이 없습니다.")
    if base_train_count + base_val_count != len(base_items):
        raise AutoLabelingError("기준 Colab export의 split은 train/val이어야 합니다.")

    session_manifest_path = scan_root / "session_manifest.json"
    inventory_path = scan_root / "video_inventory.jsonl"
    session_manifest = read_json(session_manifest_path)
    if (
        not isinstance(session_manifest, dict)
        or session_manifest.get("schema_version") != 1
    ):
        raise AutoLabelingError("지원하지 않는 session manifest입니다.")
    raw_sessions = session_manifest.get("sessions")
    if not isinstance(raw_sessions, list) or not raw_sessions:
        raise AutoLabelingError("session manifest에 세션이 없습니다.")
    inventory = read_jsonl(inventory_path)
    assignments = _read_assignments(assignments_path)
    session_by_id = {
        _required_text(item.get("session_id"), "session_id"): item
        for item in raw_sessions
        if isinstance(item, dict)
    }
    if set(assignments) != set(session_by_id):
        missing = sorted(set(session_by_id) - set(assignments))
        unknown = sorted(set(assignments) - set(session_by_id))
        detail = missing[0] if missing else unknown[0]
        raise AutoLabelingError(f"세션 배정 목록이 scan 결과와 다릅니다: {detail}")

    selected_session_ids: list[str] = []
    student_sessions: list[str] = []
    for session_id, assignment in assignments.items():
        role = assignment["role"]
        if role not in {"dataset", "excluded"}:
            raise AutoLabelingError(
                f"session_id={session_id}: val 확장에서는 dataset/excluded만 허용됩니다."
            )
        if role == "excluded":
            continue
        if assignment["requested_split"] != "val":
            raise AutoLabelingError(
                f"session_id={session_id}: val 확장은 requested_split=val이어야 합니다."
            )
        _validate_approval(assignment, session_id, allow_approved_student_data)
        selected_session_ids.append(session_id)
        if assignment["subject_category"] == "student":
            student_sessions.append(session_id)
    if not selected_session_ids:
        raise AutoLabelingError("dataset 역할의 val 세션이 한 개 이상 필요합니다.")

    sources_by_session = _inventory_by_session(inventory)
    dataset_sources: list[dict[str, Any]] = []
    source_hashes: set[str] = set()
    for session_id in sorted(selected_session_ids):
        assignment = assignments[session_id]
        records = sources_by_session.get(session_id, [])
        if not records:
            raise AutoLabelingError(f"session_id={session_id}: 채택된 영상이 없습니다.")
        for record in records:
            digest = _required_sha256(record.get("sha256"), "sha256")
            if digest in source_hashes:
                raise AutoLabelingError("동일 영상 해시가 val 확장에 중복됐습니다.")
            source_hashes.add(digest)
            dataset_sources.append(
                {
                    "source_id": _required_text(record.get("source_id"), "source_id"),
                    "file_path": _required_text(
                        record.get("absolute_path"), "absolute_path"
                    ),
                    "source_sha256": digest,
                    "approval_reference": assignment["approval_reference"],
                    "consent_scope": assignment["consent_scope"],
                    "retention_expires_at": assignment["retention_expires_at"],
                    "camera_id": _required_text(record.get("camera_id"), "camera_id"),
                    "session_id": session_id,
                    "captured_at": _required_text(
                        record.get("captured_at"), "captured_at"
                    ),
                    "subject_category": assignment["subject_category"],
                    "usage": "dataset",
                    "requested_split": "val",
                }
            )

    assignment_sha256 = sha256_file(assignments_path.resolve(strict=True))
    scan_fingerprint = _required_sha256(
        session_manifest.get("scan_fingerprint"), "scan_fingerprint"
    )
    base_manifest_sha256 = sha256_file(base_manifest_path)
    run_suffix = sha256_bytes(
        f"{scan_fingerprint}:{assignment_sha256}:{base_manifest_sha256}".encode()
    )[:12]
    run_id = f"person-dataset-{run_suffix}"

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{target.name}-", dir=target.parent
    ) as temp:
        temporary = Path(temp)
        write_json(
            temporary / "dataset_manifest.json",
            {
                "schema_version": 2,
                "manifest_role": "dataset",
                "run_id": run_id,
                "scan_fingerprint": scan_fingerprint,
                "extension_role": "validation",
                "base_export_manifest_sha256": base_manifest_sha256,
                "sources": dataset_sources,
            },
        )
        write_json(
            temporary / "extension_receipt.json",
            {
                "schema_version": 1,
                "policy": "existing-train-plus-session-disjoint-val-v1",
                "dataset_run_id": run_id,
                "scan_fingerprint": scan_fingerprint,
                "assignments_sha256": assignment_sha256,
                "base_export_manifest_sha256": base_manifest_sha256,
                "base_dataset_version": base_report.get("source_dataset_version"),
                "base_train_count": base_train_count,
                "base_val_count": base_val_count,
                "selected_val_sessions": sorted(selected_session_ids),
                "selected_source_count": len(dataset_sources),
                "student_sessions": sorted(student_sessions),
                "approved_student_flag_used": bool(student_sessions),
            },
        )
        write_json(
            temporary / "leak_check.json",
            {
                "schema_version": 1,
                "passed": True,
                "checks": {
                    "selected_sessions_are_val_only": True,
                    "selected_source_hashes_unique": True,
                    "base_export_valid": True,
                    "frame_near_duplicate_check": "required-after-frame-extraction",
                },
                "base_train_count": base_train_count,
                "base_val_count": base_val_count,
                "validation_source_count": len(dataset_sources),
            },
        )
        write_json(
            temporary / "privacy_export_required.json",
            {
                "schema_version": 1,
                "colab_export_allowed": False,
                "reason": "로컬 검수와 비식별화 후 기준 train export와 결합해야 함",
                "student_data_present": bool(student_sessions),
                "raw_video_export_allowed": False,
            },
        )
        temporary.replace(target)
    return target


def _read_assignments(path: Path) -> dict[str, dict[str, str]]:
    try:
        with path.resolve(strict=True).open(
            "r", encoding="utf-8-sig", newline=""
        ) as handle:
            reader = csv.DictReader(handle)
            required = {
                "session_id",
                "role",
                "requested_split",
                "approval_reference",
                "consent_scope",
                "retention_expires_at",
                "subject_category",
                "evaluation_scope",
            }
            if reader.fieldnames is None or not required.issubset(reader.fieldnames):
                raise AutoLabelingError("session_assignments.csv 필수 열이 없습니다.")
            assignments: dict[str, dict[str, str]] = {}
            for row in reader:
                session_id = str(row.get("session_id", "")).strip()
                if SAFE_ID_PATTERN.fullmatch(session_id) is None:
                    raise AutoLabelingError(
                        "session_assignments.csv의 session_id가 올바르지 않습니다."
                    )
                if session_id in assignments:
                    raise AutoLabelingError(
                        "session_assignments.csv에 중복 세션이 있습니다."
                    )
                assignment = {key: str(row.get(key, "")).strip() for key in required}
                if assignment["role"] not in ROLES:
                    raise AutoLabelingError(
                        f"session_id={session_id}: 알 수 없는 role입니다."
                    )
                if assignment["requested_split"] not in SPLITS | {""}:
                    raise AutoLabelingError(
                        f"session_id={session_id}: requested_split은 train/val/빈 값이어야 합니다."
                    )
                assignments[session_id] = assignment
            return assignments
    except OSError as exc:
        raise AutoLabelingError("session_assignments.csv를 읽을 수 없습니다.") from exc


def _assign_train_val(
    session_ids: list[str], assignments: dict[str, dict[str, str]]
) -> dict[str, str]:
    if not session_ids:
        return {}
    ordered = sorted(session_ids, key=lambda value: sha256_bytes(value.encode()))
    val_count = 0 if len(ordered) == 1 else max(1, round(len(ordered) * 0.1))
    automatic = {
        session_id: ("val" if index < val_count else "train")
        for index, session_id in enumerate(ordered)
    }
    for session_id in ordered:
        requested = assignments[session_id]["requested_split"]
        if requested:
            automatic[session_id] = requested
    if set(automatic.values()) == {"val"}:
        raise AutoLabelingError("dataset 세션에는 train 세션이 한 개 이상 필요합니다.")
    return automatic


def _inventory_by_session(
    inventory: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for item in inventory:
        if item.get("status") != "accepted":
            continue
        session_id = _required_text(item.get("session_id"), "session_id")
        result.setdefault(session_id, []).append(item)
    for values in result.values():
        values.sort(key=lambda item: str(item.get("captured_at", "")))
    return result


def _validate_approval(
    assignment: dict[str, str],
    session_id: str,
    allow_approved_student_data: bool,
) -> None:
    if assignment["consent_scope"] != "person-detection-training":
        raise AutoLabelingError(
            f"session_id={session_id}: consent_scope가 올바르지 않습니다."
        )
    if not assignment["approval_reference"]:
        raise AutoLabelingError(
            f"session_id={session_id}: approval_reference가 필요합니다."
        )
    category = assignment["subject_category"]
    if category not in SUBJECT_CATEGORIES:
        raise AutoLabelingError(
            f"session_id={session_id}: subject_category가 올바르지 않습니다."
        )
    if category == "student" and not allow_approved_student_data:
        raise AutoLabelingError(
            "실제 학생 데이터는 --allow-approved-student-data를 명시해야 합니다."
        )
    try:
        expires_at = datetime.fromisoformat(
            assignment["retention_expires_at"].replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise AutoLabelingError(
            f"session_id={session_id}: retention_expires_at이 올바르지 않습니다."
        ) from exc
    if expires_at.tzinfo is None:
        raise AutoLabelingError(
            f"session_id={session_id}: retention_expires_at에 timezone이 필요합니다."
        )
    if expires_at <= datetime.now(UTC):
        raise AutoLabelingError(
            f"session_id={session_id}: retention_expires_at이 이미 지났습니다."
        )


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise AutoLabelingError(f"{name}이 비어 있습니다.")
    return value


def _required_sha256(value: object, name: str) -> str:
    text = _required_text(value, name)
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise AutoLabelingError(f"{name}이 SHA-256 형식이 아닙니다.")
    return text
