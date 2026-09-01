from __future__ import annotations

import json
import math
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml

from .core import (
    SAFE_ID_PATTERN,
    Settings,
    frame_id_from_record,
    load_settings,
    read_json,
    read_jsonl,
    sha256_bytes,
    sha256_file,
    utc_now_iso,
    verified_frame_image_path,
    write_json,
    write_jsonl,
)
from .deduplication import (
    DeduplicationInput,
    deduplicate_frames,
    deduplication_policy,
    duplicate_group_id,
)
from .errors import AutoLabelingError
from .prelabel import verify_prelabel_artifacts
from .review import verify_review_receipt
from .yolo import parse_yolo_file

DATASET_PATTERN = re.compile(r"^person-v(\d{4})$")
LEGACY_SPLITS = ("train", "val", "test")
CURRENT_SPLITS = ("train", "val")


def default_datasets_root() -> Path:
    return (
        Path(__file__).resolve().parent.parent / "data" / "auto-labeling" / "datasets"
    )


def publish_dataset(
    run_dir: Path,
    *,
    dataset_root: Path | None = None,
    settings: Settings | None = None,
) -> Path:
    active_settings = settings or load_settings()
    run_dir = run_dir.resolve(strict=True)
    datasets_root = (dataset_root or default_datasets_root()).resolve()
    datasets_root.mkdir(parents=True, exist_ok=True)
    run_manifest = _require_dict(read_json(run_dir / "run.json"), "run.json")
    prelabel_manifest = _require_dict(
        read_json(run_dir / "prelabel.json"), "prelabel.json"
    )
    frames = read_jsonl(run_dir / "frames.jsonl")
    verify_prelabel_artifacts(run_dir, manifest=prelabel_manifest, frames=frames)
    all_frame_ids = {_frame_id(frame) for frame in frames}
    review_dir, batch, receipt = _select_publishable_review(run_dir, all_frame_ids)
    run_fingerprint = _run_fingerprint(run_dir, review_dir, active_settings)
    existing = _find_existing_publication(
        datasets_root, str(run_manifest.get("run_id", "")), run_fingerprint
    )
    if existing is not None:
        validate_dataset(existing)
        return existing

    dataset_version = _next_dataset_version(datasets_root)
    dataset_dir = datasets_root / dataset_version
    if dataset_dir.exists():
        raise AutoLabelingError("새 데이터셋 버전 경로가 이미 있습니다.")
    reviewed_ids = set(_string_list(batch.get("frame_ids"), "frame_ids"))
    auto_accepted_ids = set(
        _string_list(
            batch.get("auto_accepted_frame_ids", []), "auto_accepted_frame_ids"
        )
    )
    if (
        reviewed_ids | auto_accepted_ids != all_frame_ids
        or reviewed_ids & auto_accepted_ids
    ):
        raise AutoLabelingError(
            "검수·자동 승인 프레임이 전체 실행을 정확히 덮지 않습니다."
        )
    frame_by_id = {_frame_id(frame): frame for frame in frames}
    receipt_files = {
        _require_string(item.get("frame_id"), "frame_id"): item
        for item in receipt.get("files", [])
        if isinstance(item, dict)
    }
    publication_sources = _publication_sources(
        run_dir,
        review_dir,
        frames,
        reviewed_ids,
        receipt_files,
    )
    deduplication = deduplicate_frames(
        list(publication_sources.values()), active_settings
    )
    retained_frame_ids = set(deduplication.retained_frame_ids)
    retained_frames = [
        frame for frame in frames if _frame_id(frame) in retained_frame_ids
    ]
    session_splits = _assign_session_splits(retained_frames)
    status = "ready" if len(session_splits) >= 10 else "pilot"

    with tempfile.TemporaryDirectory(
        prefix=f".{dataset_version}-", dir=datasets_root
    ) as temporary:
        temporary_dir = Path(temporary)
        for split in CURRENT_SPLITS:
            (temporary_dir / "images" / split).mkdir(parents=True)
            (temporary_dir / "labels" / split).mkdir(parents=True)
        items: list[dict[str, object]] = []
        for frame_id in sorted(retained_frame_ids):
            frame = frame_by_id[frame_id]
            session_id = _require_string(frame.get("session_id"), "session_id")
            split = session_splits[session_id]
            publication_source = publication_sources[frame_id]
            source_image = publication_source.image_path
            source_label = publication_source.label_path
            approval_type = publication_source.approval_type
            target_image = temporary_dir / "images" / split / f"{frame_id}.jpg"
            target_label = temporary_dir / "labels" / split / f"{frame_id}.txt"
            shutil.copy2(source_image, target_image)
            shutil.copy2(source_label, target_label)
            items.append(
                {
                    "frame_id": frame_id,
                    "source_id": frame.get("source_id"),
                    "source_sha256": frame.get("source_sha256"),
                    "camera_id": frame.get("camera_id"),
                    "session_id": session_id,
                    "timestamp_ms": frame.get("timestamp_ms"),
                    "approval_reference": frame.get("approval_reference"),
                    "consent_scope": frame.get("consent_scope"),
                    "retention_expires_at": frame.get("retention_expires_at"),
                    "subject_category": frame.get("subject_category"),
                    "approved_student_data": bool(
                        run_manifest.get("approved_student_data", False)
                    ),
                    "requested_split": frame.get("requested_split"),
                    "approval_type": approval_type,
                    "duplicate_group_id": deduplication.group_id_by_representative.get(
                        frame_id
                    ),
                    "split": split,
                    "image_path": f"images/{split}/{frame_id}.jpg",
                    "label_path": f"labels/{split}/{frame_id}.txt",
                    "image_sha256": sha256_file(target_image),
                    "label_sha256": sha256_file(target_label),
                }
            )
        _write_data_yaml(temporary_dir / "data.yaml")
        deduplication_path = temporary_dir / "deduplication.jsonl"
        write_jsonl(deduplication_path, deduplication.report_rows)
        deduplication_manifest = {
            **deduplication_policy(active_settings),
            "input_frame_count": deduplication.input_frame_count,
            "retained_frame_count": deduplication.retained_frame_count,
            "removed_frame_count": deduplication.removed_frame_count,
            "duplicate_group_count": len(deduplication.report_rows),
            "report_path": "deduplication.jsonl",
            "report_sha256": sha256_file(deduplication_path),
        }
        write_json(
            temporary_dir / "manifest.json",
            {
                "schema_version": 3,
                "dataset_version": dataset_version,
                "status": status,
                "class_names": ["person"],
                "source_run_id": run_manifest.get("run_id"),
                "run_fingerprint": run_fingerprint,
                "sampling_policy_version": run_manifest.get("sampling_policy_version"),
                "model": prelabel_manifest.get("model"),
                "review": {
                    "batch_id": batch.get("batch_id"),
                    "reviewer_id": receipt.get("reviewer_id"),
                    "receipt_sha256": sha256_file(review_dir / "review-completed.json"),
                    "quality_gate": receipt.get("quality_gate"),
                },
                "deduplication": deduplication_manifest,
                "session_splits": session_splits,
                "privacy": {
                    "raw_video_export_allowed": False,
                    "colab_export_requires_privacy_receipt": True,
                    "approved_student_data": bool(
                        run_manifest.get("approved_student_data", False)
                    ),
                },
                "published_at": utc_now_iso(),
                "items": items,
            },
        )
        validate_dataset(temporary_dir, expected_version=dataset_version)
        temporary_dir.replace(dataset_dir)
    return dataset_dir


def validate_dataset(
    dataset_dir: Path, *, expected_version: str | None = None
) -> dict[str, object]:
    dataset_dir = dataset_dir.resolve(strict=True)
    manifest = _require_dict(read_json(dataset_dir / "manifest.json"), "manifest.json")
    schema_version = manifest.get("schema_version")
    if schema_version not in {1, 2, 3}:
        raise AutoLabelingError("지원하지 않는 데이터셋 manifest schema입니다.")
    dataset_version = _require_string(
        manifest.get("dataset_version"), "dataset_version"
    )
    directory_version = expected_version or dataset_dir.name
    if (
        dataset_version != directory_version
        or DATASET_PATTERN.fullmatch(dataset_version) is None
    ):
        raise AutoLabelingError("데이터셋 버전과 디렉터리 이름이 다릅니다.")
    if manifest.get("class_names") != ["person"]:
        raise AutoLabelingError("manifest 클래스 계약은 person 하나여야 합니다.")
    _require_sha256(manifest.get("run_fingerprint"), "run_fingerprint")
    model = _require_dict(manifest.get("model"), "model")
    _require_sha256(model.get("model_sha256"), "model.model_sha256")
    review = _require_dict(manifest.get("review"), "review")
    _require_sha256(review.get("receipt_sha256"), "review.receipt_sha256")
    quality_gate = _require_dict(review.get("quality_gate"), "quality_gate")
    if quality_gate.get("passed") is not True:
        raise AutoLabelingError(
            "통과하지 못한 검수 품질 결과가 발행 manifest에 있습니다."
        )
    try:
        data_config = yaml.safe_load(
            (dataset_dir / "data.yaml").read_text(encoding="utf-8")
        )
    except (OSError, yaml.YAMLError) as exc:
        raise AutoLabelingError("data.yaml을 읽을 수 없습니다.") from exc
    if not isinstance(data_config, dict):
        raise AutoLabelingError("data.yaml은 YAML 객체여야 합니다.")
    if data_config.get("names") != {0: "person"} or data_config.get("nc") != 1:
        raise AutoLabelingError("data.yaml 클래스 계약은 0: person 하나여야 합니다.")
    if data_config.get("path") != ".":
        raise AutoLabelingError("data.yaml의 기준 경로는 현재 데이터셋이어야 합니다.")
    splits = CURRENT_SPLITS if schema_version == 3 else LEGACY_SPLITS
    for split in splits:
        if data_config.get(split) != f"images/{split}":
            raise AutoLabelingError(f"data.yaml의 {split} 경로가 올바르지 않습니다.")
    items = manifest.get("items")
    if not isinstance(items, list) or not items:
        raise AutoLabelingError("manifest.json에 발행 항목이 없습니다.")
    frame_ids: set[str] = set()
    image_hashes: dict[str, str] = {}
    session_splits: dict[str, str] = {}
    split_counts = {split: 0 for split in splits}
    for raw_item in items:
        item = _require_dict(raw_item, "item")
        frame_id = _require_string(item.get("frame_id"), "frame_id")
        split = _require_string(item.get("split"), "split")
        session_id = _require_string(item.get("session_id"), "session_id")
        if split not in splits:
            raise AutoLabelingError(f"frame_id={frame_id}: 알 수 없는 split입니다.")
        if frame_id in frame_ids:
            raise AutoLabelingError("manifest.json에 중복 frame_id가 있습니다.")
        frame_ids.add(frame_id)
        previous_split = session_splits.setdefault(session_id, split)
        if previous_split != split:
            raise AutoLabelingError("같은 session_id가 여러 split에 있습니다.")
        image_path = dataset_dir / f"images/{split}/{frame_id}.jpg"
        label_path = dataset_dir / f"labels/{split}/{frame_id}.txt"
        if item.get("image_path") != f"images/{split}/{frame_id}.jpg":
            raise AutoLabelingError("manifest 이미지 경로가 frame 계약과 다릅니다.")
        if item.get("label_path") != f"labels/{split}/{frame_id}.txt":
            raise AutoLabelingError("manifest 라벨 경로가 frame 계약과 다릅니다.")
        if not image_path.is_file() or not label_path.is_file():
            raise AutoLabelingError(
                f"frame_id={frame_id}: 이미지 또는 라벨이 없습니다."
            )
        image_sha256 = sha256_file(image_path)
        label_sha256 = sha256_file(label_path)
        if (
            item.get("image_sha256") != image_sha256
            or item.get("label_sha256") != label_sha256
        ):
            raise AutoLabelingError(
                f"frame_id={frame_id}: manifest 해시와 파일이 다릅니다."
            )
        duplicate_frame = image_hashes.get(image_sha256)
        if duplicate_frame is not None and duplicate_frame != frame_id:
            raise AutoLabelingError("서로 다른 frame_id에 같은 이미지 해시가 있습니다.")
        image_hashes[image_sha256] = frame_id
        parse_yolo_file(label_path)
        _validate_item_approval(item, frame_id, schema_version=schema_version)
        split_counts[split] += 1
    for split in splits:
        actual_images = {
            path.stem for path in (dataset_dir / "images" / split).glob("*.jpg")
        }
        actual_labels = {
            path.stem for path in (dataset_dir / "labels" / split).glob("*.txt")
        }
        expected = {
            _require_string(item.get("frame_id"), "frame_id")
            for item in items
            if isinstance(item, dict) and item.get("split") == split
        }
        if actual_images != expected or actual_labels != expected:
            raise AutoLabelingError(f"{split} 파일 집합이 manifest와 다릅니다.")
    declared_session_splits = _require_dict(
        manifest.get("session_splits"), "session_splits"
    )
    if declared_session_splits != session_splits:
        raise AutoLabelingError("manifest의 session split 목록이 항목과 다릅니다.")
    if schema_version == 3:
        expected_splits = _assign_session_splits(
            [
                {
                    "session_id": _require_string(item.get("session_id"), "session_id"),
                    "requested_split": item.get("requested_split"),
                }
                for item in items
                if isinstance(item, dict)
            ]
        )
    else:
        expected_splits = _assign_legacy_session_splits(
            [{"session_id": session_id} for session_id in session_splits]
        )
    if session_splits != expected_splits:
        policy = "90/10 train/val" if schema_version == 3 else "80/10/10"
        raise AutoLabelingError(f"session split이 고정 {policy} 정책과 다릅니다.")
    expected_status = "ready" if len(session_splits) >= 10 else "pilot"
    if manifest.get("status") != expected_status:
        raise AutoLabelingError("세션 수와 데이터셋 상태가 다릅니다.")
    if schema_version in {2, 3}:
        deduplication_report = _validate_deduplication(
            dataset_dir, manifest, items, frame_ids
        )
    else:
        deduplication_report = {
            "input_frame_count": len(frame_ids),
            "retained_frame_count": len(frame_ids),
            "removed_frame_count": 0,
            "duplicate_group_count": 0,
        }
    return {
        "dataset_version": dataset_version,
        "status": manifest.get("status"),
        "frame_count": len(frame_ids),
        "split_counts": split_counts,
        "session_count": len(session_splits),
        "deduplication": deduplication_report,
    }


def _publication_sources(
    run_dir: Path,
    review_dir: Path,
    frames: list[dict[str, Any]],
    reviewed_ids: set[str],
    receipt_files: dict[str, dict[str, Any]],
) -> dict[str, DeduplicationInput]:
    sources: dict[str, DeduplicationInput] = {}
    for frame in frames:
        frame_id = _frame_id(frame)
        image_path = verified_frame_image_path(run_dir, frame)
        if frame_id in reviewed_ids:
            label_path = review_dir / f"{frame_id}.txt"
            approval_type = "human-reviewed"
            receipt_file = receipt_files.get(frame_id)
            if receipt_file is None or receipt_file.get("image_sha256") != sha256_file(
                image_path
            ):
                raise AutoLabelingError(
                    f"frame_id={frame_id}: 검수 이미지와 발행 이미지가 다릅니다."
                )
        else:
            label_path = run_dir / "candidate-labels" / f"{frame_id}.txt"
            approval_type = "calibrated-auto-accept"
        sources[frame_id] = DeduplicationInput(
            frame_id=frame_id,
            camera_id=_require_string(frame.get("camera_id"), "camera_id"),
            session_id=_require_string(frame.get("session_id"), "session_id"),
            image_path=image_path,
            label_path=label_path,
            image_sha256=_require_sha256(
                frame.get("image_sha256"), f"frame_id={frame_id}.image_sha256"
            ),
            approval_type=approval_type,
        )
    return sources


def _validate_deduplication(
    dataset_dir: Path,
    manifest: dict[str, Any],
    items: list[Any],
    retained_frame_ids: set[str],
) -> dict[str, int]:
    summary = _require_dict(manifest.get("deduplication"), "deduplication")
    policy_version = _require_string(summary.get("policy_version"), "policy_version")
    if (
        summary.get("exact_scope") != "all-cameras"
        or summary.get("visual_scope") != "same-camera"
        or summary.get("phash_algorithm") != "dct-64"
        or summary.get("representative_policy")
        != "human-reviewed-then-sharpness-then-frame-id"
    ):
        raise AutoLabelingError("중복 제거 정책 계약이 올바르지 않습니다.")
    phash_threshold = _require_nonnegative_int(
        summary.get("phash_hamming_threshold"), "phash_hamming_threshold"
    )
    comparison_size = _require_nonnegative_int(
        summary.get("comparison_size"), "comparison_size"
    )
    pixel_threshold = _require_probability(
        summary.get("pixel_mae_threshold"), "pixel_mae_threshold"
    )
    bbox_threshold = _require_probability(
        summary.get("bbox_iou_threshold"), "bbox_iou_threshold"
    )
    if phash_threshold > 64 or comparison_size < 8:
        raise AutoLabelingError("중복 제거 pHash·이미지 크기 설정이 올바르지 않습니다.")

    input_count = _require_nonnegative_int(
        summary.get("input_frame_count"), "input_frame_count"
    )
    retained_count = _require_nonnegative_int(
        summary.get("retained_frame_count"), "retained_frame_count"
    )
    removed_count = _require_nonnegative_int(
        summary.get("removed_frame_count"), "removed_frame_count"
    )
    group_count = _require_nonnegative_int(
        summary.get("duplicate_group_count"), "duplicate_group_count"
    )
    if summary.get("report_path") != "deduplication.jsonl":
        raise AutoLabelingError("중복 제거 보고서 경로가 올바르지 않습니다.")
    report_path = dataset_dir / "deduplication.jsonl"
    if not report_path.is_file() or report_path.is_symlink():
        raise AutoLabelingError("deduplication.jsonl이 없거나 링크입니다.")
    if summary.get("report_sha256") != sha256_file(report_path):
        raise AutoLabelingError("중복 제거 보고서 해시가 manifest와 다릅니다.")
    report_rows = read_jsonl(report_path)
    if len(report_rows) != group_count:
        raise AutoLabelingError("중복 그룹 수가 manifest와 다릅니다.")

    item_by_frame = {
        _require_string(item.get("frame_id"), "frame_id"): item
        for item in items
        if isinstance(item, dict)
    }
    representative_groups: dict[str, str] = {}
    removed_frame_ids: set[str] = set()
    group_ids: set[str] = set()
    for row in report_rows:
        if row.get("schema_version") != 1:
            raise AutoLabelingError("지원하지 않는 중복 그룹 schema입니다.")
        group_id = _require_string(row.get("group_id"), "group_id")
        if re.fullmatch(r"[0-9a-f]{24}", group_id) is None or group_id in group_ids:
            raise AutoLabelingError("중복 그룹 ID가 올바르지 않거나 중복됩니다.")
        group_ids.add(group_id)
        representative = _validate_deduplication_frame(
            _require_dict(row.get("representative"), "representative")
        )
        representative_id = str(representative["frame_id"])
        if (
            representative_id not in retained_frame_ids
            or representative_id in representative_groups
        ):
            raise AutoLabelingError("중복 그룹 대표 프레임이 발행 항목과 다릅니다.")
        representative_groups[representative_id] = group_id
        item = item_by_frame[representative_id]
        if representative["image_sha256"] != item.get("image_sha256"):
            raise AutoLabelingError(
                "중복 그룹 대표 이미지 해시가 발행 항목과 다릅니다."
            )
        raw_duplicates = row.get("duplicates")
        if not isinstance(raw_duplicates, list) or not raw_duplicates:
            raise AutoLabelingError(
                "중복 그룹에는 제외 프레임이 한 개 이상 있어야 합니다."
            )
        duplicate_frames = [
            _validate_deduplication_frame(_require_dict(value, "duplicate"))
            for value in raw_duplicates
        ]
        expected_selection_reason = _deduplication_selection_reason(
            representative, duplicate_frames
        )
        if row.get("representative_selection_reason") != expected_selection_reason:
            raise AutoLabelingError(
                "중복 그룹 대표 프레임 선정 근거가 올바르지 않습니다."
            )
        member_by_id = {
            representative_id: representative,
            **{str(value["frame_id"]): value for value in duplicate_frames},
        }
        member_ids = list(member_by_id)
        if len(member_ids) != len(raw_duplicates) + 1:
            raise AutoLabelingError("중복 그룹 안에 같은 frame_id가 여러 번 있습니다.")
        if duplicate_group_id(policy_version, member_ids) != group_id:
            raise AutoLabelingError("중복 그룹 ID가 구성 프레임과 다릅니다.")
        for raw_duplicate, duplicate in zip(
            raw_duplicates, duplicate_frames, strict=True
        ):
            duplicate_id = str(duplicate["frame_id"])
            if duplicate_id in retained_frame_ids or duplicate_id in removed_frame_ids:
                raise AutoLabelingError(
                    "제외 프레임이 발행되었거나 여러 그룹에 있습니다."
                )
            removed_frame_ids.add(duplicate_id)
            matched_id = _require_string(
                raw_duplicate.get("matched_against_frame_id"),
                "matched_against_frame_id",
            )
            if matched_id == duplicate_id or matched_id not in member_by_id:
                raise AutoLabelingError(
                    "중복 프레임의 비교 기준 프레임이 올바르지 않습니다."
                )
            matched = member_by_id[matched_id]
            match_type = _require_string(raw_duplicate.get("match_type"), "match_type")
            phash_distance = _require_nonnegative_int(
                raw_duplicate.get("phash_hamming_distance"),
                "phash_hamming_distance",
            )
            pixel_mae = _require_probability(
                raw_duplicate.get("pixel_mae"), "pixel_mae"
            )
            bbox_min_iou = _require_probability(
                raw_duplicate.get("bbox_min_iou"), "bbox_min_iou"
            )
            actual_phash_distance = (
                int(str(duplicate["phash"]), 16) ^ int(str(matched["phash"]), 16)
            ).bit_count()
            if phash_distance != actual_phash_distance:
                raise AutoLabelingError("중복 프레임의 pHash 거리가 보고서와 다릅니다.")
            if match_type == "exact-sha256":
                if (
                    duplicate["image_sha256"] != matched["image_sha256"]
                    or phash_distance != 0
                    or pixel_mae != 0.0
                    or bbox_min_iou < 1.0 - 1e-8
                ):
                    raise AutoLabelingError(
                        "exact 중복 프레임 근거가 올바르지 않습니다."
                    )
            elif match_type == "visual-same-camera":
                if (
                    duplicate["camera_id"] != matched["camera_id"]
                    or duplicate["image_sha256"] == matched["image_sha256"]
                    or phash_distance > phash_threshold
                    or pixel_mae > pixel_threshold
                    or bbox_min_iou < bbox_threshold
                ):
                    raise AutoLabelingError(
                        "시각적 중복 프레임 근거가 임계값과 다릅니다."
                    )
            else:
                raise AutoLabelingError("알 수 없는 중복 판정 유형입니다.")
            if _deduplication_priority(duplicate) < _deduplication_priority(
                representative
            ):
                raise AutoLabelingError(
                    "중복 그룹 대표 프레임 선정 순서가 올바르지 않습니다."
                )

    for frame_id, item in item_by_frame.items():
        if item.get("duplicate_group_id") != representative_groups.get(frame_id):
            raise AutoLabelingError("발행 항목의 중복 그룹 ID가 보고서와 다릅니다.")
    if (
        retained_count != len(retained_frame_ids)
        or removed_count != len(removed_frame_ids)
        or input_count != retained_count + removed_count
    ):
        raise AutoLabelingError("중복 제거 프레임 수가 manifest와 다릅니다.")
    return {
        "input_frame_count": input_count,
        "retained_frame_count": retained_count,
        "removed_frame_count": removed_count,
        "duplicate_group_count": group_count,
    }


def _validate_deduplication_frame(value: dict[str, Any]) -> dict[str, object]:
    frame_id = _require_string(value.get("frame_id"), "frame_id")
    camera_id = _require_string(value.get("camera_id"), "camera_id")
    session_id = _require_string(value.get("session_id"), "session_id")
    if not all(
        SAFE_ID_PATTERN.fullmatch(identifier)
        for identifier in (frame_id, camera_id, session_id)
    ):
        raise AutoLabelingError("중복 제거 보고서의 ID 형식이 올바르지 않습니다.")
    approval_type = _require_string(value.get("approval_type"), "approval_type")
    if approval_type not in {"human-reviewed", "calibrated-auto-accept"}:
        raise AutoLabelingError("중복 제거 보고서의 승인 유형이 올바르지 않습니다.")
    phash = _require_string(value.get("phash"), "phash")
    if re.fullmatch(r"[0-9a-f]{16}", phash) is None:
        raise AutoLabelingError("중복 제거 보고서의 pHash가 올바르지 않습니다.")
    sharpness = value.get("sharpness")
    if (
        not isinstance(sharpness, (int, float))
        or isinstance(sharpness, bool)
        or not math.isfinite(float(sharpness))
        or float(sharpness) < 0
    ):
        raise AutoLabelingError("중복 제거 보고서의 선명도가 올바르지 않습니다.")
    return {
        "frame_id": frame_id,
        "camera_id": camera_id,
        "session_id": session_id,
        "approval_type": approval_type,
        "image_sha256": _require_sha256(value.get("image_sha256"), "image_sha256"),
        "phash": phash,
        "sharpness": float(sharpness),
    }


def _deduplication_priority(value: dict[str, object]) -> tuple[int, float, str]:
    human_priority = 0 if value["approval_type"] == "human-reviewed" else 1
    return (
        human_priority,
        -float(cast(float, value["sharpness"])),
        str(value["frame_id"]),
    )


def _deduplication_selection_reason(
    representative: dict[str, object], duplicates: list[dict[str, object]]
) -> str:
    if representative["approval_type"] == "human-reviewed" and any(
        frame["approval_type"] != "human-reviewed" for frame in duplicates
    ):
        return "human-reviewed"
    if any(
        float(cast(float, representative["sharpness"]))
        > float(cast(float, frame["sharpness"]))
        for frame in duplicates
    ):
        return "highest-sharpness"
    return "lowest-frame-id"


def _select_publishable_review(
    run_dir: Path, all_frame_ids: set[str]
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    candidates: list[tuple[str, Path, dict[str, Any], dict[str, Any]]] = []
    review_root = run_dir / "review"
    if not review_root.is_dir():
        raise AutoLabelingError("검수 디렉터리가 없습니다.")
    for review_dir in sorted(path for path in review_root.iterdir() if path.is_dir()):
        receipt_path = review_dir / "review-completed.json"
        if not receipt_path.is_file():
            continue
        receipt = verify_review_receipt(review_dir)
        batch = _require_dict(
            read_json(review_dir / "review-batch.json"), "review-batch.json"
        )
        quality_gate = _require_dict(receipt.get("quality_gate"), "quality_gate")
        reviewed = set(_string_list(batch.get("frame_ids"), "frame_ids"))
        auto_accepted = set(
            _string_list(
                batch.get("auto_accepted_frame_ids", []), "auto_accepted_frame_ids"
            )
        )
        if quality_gate.get("passed") is not True:
            continue
        if reviewed | auto_accepted != all_frame_ids or reviewed & auto_accepted:
            continue
        completed_at = _require_string(receipt.get("completed_at"), "completed_at")
        candidates.append((completed_at, review_dir, batch, receipt))
    if not candidates:
        raise AutoLabelingError("발행 가능한 검수 완료 배치가 없습니다.")
    _, review_dir, batch, receipt = max(candidates, key=lambda item: item[0])
    return review_dir, batch, receipt


def _assign_session_splits(frames: list[dict[str, Any]]) -> dict[str, str]:
    requested: dict[str, str] = {}
    for frame in frames:
        session_id = _require_string(frame.get("session_id"), "session_id")
        split = frame.get("requested_split")
        if split is None:
            continue
        if split not in CURRENT_SPLITS:
            raise AutoLabelingError("requested_split은 train 또는 val이어야 합니다.")
        previous = requested.setdefault(session_id, split)
        if previous != split:
            raise AutoLabelingError(
                "같은 session_id에 서로 다른 requested_split이 있습니다."
            )
    sessions = sorted(
        {_require_string(frame.get("session_id"), "session_id") for frame in frames},
        key=lambda value: sha256_bytes(value.encode()),
    )
    count = len(sessions)
    val_count = 0 if count <= 1 else max(1, round(count * 0.1))
    splits: dict[str, str] = {}
    for index, session_id in enumerate(sessions):
        splits[session_id] = requested.get(
            session_id, "val" if index < val_count else "train"
        )
    if splits and set(splits.values()) == {"val"}:
        raise AutoLabelingError("train 세션이 한 개 이상 필요합니다.")
    return splits


def _assign_legacy_session_splits(
    frames: list[dict[str, Any]],
) -> dict[str, str]:
    sessions = sorted(
        {_require_string(frame.get("session_id"), "session_id") for frame in frames},
        key=lambda value: sha256_bytes(value.encode()),
    )
    count = len(sessions)
    if count == 1:
        test_count = val_count = 0
    elif count == 2:
        test_count, val_count = 0, 1
    elif count < 10:
        test_count = val_count = 1
    else:
        test_count = max(1, round(count * 0.1))
        val_count = max(1, round(count * 0.1))
    return {
        session_id: (
            "test"
            if index < test_count
            else "val"
            if index < test_count + val_count
            else "train"
        )
        for index, session_id in enumerate(sessions)
    }


def _write_data_yaml(path: Path) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "path": ".",
                "train": "images/train",
                "val": "images/val",
                "nc": 1,
                "names": {0: "person"},
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
        newline="\n",
    )


def _run_fingerprint(run_dir: Path, review_dir: Path, settings: Settings) -> str:
    values = [
        sha256_file(run_dir / "run.json"),
        sha256_file(run_dir / "frames.jsonl"),
        sha256_file(run_dir / "prelabel.json"),
        sha256_file(run_dir / "predictions.jsonl"),
        sha256_file(review_dir / "review-batch.json"),
        sha256_file(review_dir / "review-completed.json"),
        sha256_bytes(
            json.dumps(
                deduplication_policy(settings),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ),
    ]
    return sha256_bytes(":".join(values).encode())


def _find_existing_publication(
    datasets_root: Path, run_id: str, run_fingerprint: str
) -> Path | None:
    for path in sorted(datasets_root.glob("person-v????")):
        manifest_path = path / "manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = _require_dict(read_json(manifest_path), "manifest.json")
        if manifest.get("source_run_id") != run_id:
            continue
        if manifest.get("run_fingerprint") != run_fingerprint:
            if manifest.get("schema_version") == 1:
                continue
            raise AutoLabelingError("같은 run이 다른 내용으로 이미 발행됐습니다.")
        return path
    return None


def _next_dataset_version(datasets_root: Path) -> str:
    versions = [
        int(match.group(1))
        for path in datasets_root.iterdir()
        if path.is_dir() and (match := DATASET_PATTERN.fullmatch(path.name))
    ]
    next_version = max(versions, default=0) + 1
    if next_version > 9999:
        raise AutoLabelingError("데이터셋 버전 번호가 허용 범위를 넘었습니다.")
    return f"person-v{next_version:04d}"


def _validate_item_approval(
    item: dict[str, Any], frame_id: str, *, schema_version: int
) -> None:
    for field_name in ("approval_reference", "consent_scope", "retention_expires_at"):
        _require_string(item.get(field_name), field_name)
    if item.get("consent_scope") != "person-detection-training":
        raise AutoLabelingError(f"frame_id={frame_id}: 동의 범위가 올바르지 않습니다.")
    allowed_categories = {"synthetic", "consenting-adult"}
    if schema_version == 3 and item.get("approved_student_data") is True:
        allowed_categories.add("student")
    if item.get("subject_category") not in allowed_categories:
        raise AutoLabelingError(f"frame_id={frame_id}: 허용되지 않은 대상 영상입니다.")
    if item.get("approval_type") not in {"human-reviewed", "calibrated-auto-accept"}:
        raise AutoLabelingError(f"frame_id={frame_id}: 승인 유형이 올바르지 않습니다.")
    _require_sha256(item.get("source_sha256"), f"frame_id={frame_id}.source_sha256")
    try:
        expires_at = datetime.fromisoformat(
            _require_string(
                item.get("retention_expires_at"), "retention_expires_at"
            ).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise AutoLabelingError(
            f"frame_id={frame_id}: 보존 만료 시각이 올바르지 않습니다."
        ) from exc
    if expires_at.tzinfo is None or expires_at <= datetime.now(UTC):
        raise AutoLabelingError(f"frame_id={frame_id}: 보존 만료 시각이 지났습니다.")


def _require_dict(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AutoLabelingError(f"{field_name}은 객체여야 합니다.")
    return value


def _require_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise AutoLabelingError(f"{field_name}은 비어 있지 않은 문자열이어야 합니다.")
    return value


def _require_sha256(value: object, field_name: str) -> str:
    text = _require_string(value, field_name)
    if re.fullmatch(r"[0-9a-f]{64}", text) is None:
        raise AutoLabelingError(f"{field_name}은 SHA-256 형식이어야 합니다.")
    return text


def _require_nonnegative_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise AutoLabelingError(f"{field_name}은 0 이상의 정수여야 합니다.")
    return value


def _require_probability(value: object, field_name: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or not 0 <= float(value) <= 1
    ):
        raise AutoLabelingError(f"{field_name}은 0~1 유한수여야 합니다.")
    return float(value)


def _string_list(value: object, field_name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise AutoLabelingError(f"{field_name}은 문자열 배열이어야 합니다.")
    if len(value) != len(set(value)):
        raise AutoLabelingError(f"{field_name}에 중복 값이 있습니다.")
    return value


def _frame_id(frame: dict[str, Any]) -> str:
    return frame_id_from_record(frame)
