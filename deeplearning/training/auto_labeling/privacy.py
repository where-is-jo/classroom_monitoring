from __future__ import annotations

import shutil
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import cv2
import numpy as np
from numpy.typing import NDArray

from .core import read_json, sha256_file, utc_now_iso, write_json
from .errors import AutoLabelingError
from .preprocessing import (
    DEFAULT_PIXELATION_BLOCK_SIZE,
    LEGACY_COMBINED_PIXELATION,
    ORIGINAL_FRAME,
    PERSON_BBOX_TOP_PIXELATION,
    UNIFORM_FULL_FRAME_PIXELATION,
    apply_training_preprocessing,
    original_frame_contract,
    uniform_pixelation_contract,
)
from .publish import validate_dataset
from .review import verify_review_receipt
from .yolo import parse_yolo_file


def export_deidentified_dataset(
    dataset_dir: Path,
    output_dir: Path,
    *,
    operator_id: str,
    manual_privacy_review_confirmed: bool = False,
    approved_cohort_policy: str | None = None,
    head_fraction: float = 0.3,
    preprocessing_method: str = UNIFORM_FULL_FRAME_PIXELATION,
    pixelation_block_size: int = DEFAULT_PIXELATION_BLOCK_SIZE,
) -> Path:
    """라벨 독립 비식별 또는 승인된 원본 프레임 학습 사본을 만든다."""

    if not operator_id.strip():
        raise AutoLabelingError("operator_id가 필요합니다.")
    cohort_policy = (approved_cohort_policy or "").strip()
    if manual_privacy_review_confirmed and cohort_policy:
        raise AutoLabelingError(
            "수동 검토와 승인 집단 정책을 동시에 지정할 수 없습니다."
        )
    if not manual_privacy_review_confirmed and not cohort_policy:
        raise AutoLabelingError(
            "수동 개인정보 검토 또는 승인된 학생 집단 정책이 필요합니다."
        )
    approval_mode = (
        "manual-review"
        if manual_privacy_review_confirmed
        else "approved-student-cohort-policy"
    )
    if (
        preprocessing_method == ORIGINAL_FRAME
        and approval_mode != "approved-student-cohort-policy"
    ):
        raise AutoLabelingError(
            "원본 프레임 반출은 승인된 학생 집단 정책으로만 허용됩니다."
        )
    if not 0.2 <= head_fraction <= 0.5:
        raise AutoLabelingError("head_fraction은 0.2~0.5여야 합니다.")
    if preprocessing_method not in {
        UNIFORM_FULL_FRAME_PIXELATION,
        ORIGINAL_FRAME,
        PERSON_BBOX_TOP_PIXELATION,
    }:
        raise AutoLabelingError("지원하지 않는 개인정보 전처리 방식입니다.")
    if not 2 <= pixelation_block_size <= 32:
        raise AutoLabelingError("pixelation_block_size는 2~32여야 합니다.")
    preprocessing_contract = _preprocessing_contract(
        preprocessing_method,
        head_fraction=head_fraction,
        pixelation_block_size=pixelation_block_size,
    )
    source = dataset_dir.resolve(strict=True)
    report = validate_dataset(source)
    manifest = read_json(source / "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 3:
        raise AutoLabelingError("Colab export는 schema v3 데이터셋만 지원합니다.")
    if preprocessing_method == ORIGINAL_FRAME:
        _validate_original_frame_approvals(manifest)
    target = output_dir.resolve()
    if target.exists():
        raise AutoLabelingError("학습 export 출력 디렉터리가 이미 있습니다.")

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{target.name}-", dir=target.parent
    ) as temp:
        temporary = Path(temp)
        sanitized_items: list[dict[str, object]] = []
        for raw_item in manifest.get("items", []):
            if not isinstance(raw_item, dict):
                raise AutoLabelingError("dataset manifest item이 객체가 아닙니다.")
            frame_id = str(raw_item.get("frame_id", ""))
            split = str(raw_item.get("split", ""))
            source_image = source / str(raw_item.get("image_path", ""))
            source_label = source / str(raw_item.get("label_path", ""))
            target_image = temporary / "images" / split / f"{frame_id}.jpg"
            target_label = temporary / "labels" / split / f"{frame_id}.txt"
            target_image.parent.mkdir(parents=True, exist_ok=True)
            target_label.parent.mkdir(parents=True, exist_ok=True)
            _write_deidentified_image(
                source_image,
                source_label,
                target_image,
                frame_id=frame_id,
                head_fraction=head_fraction,
                preprocessing_contract=preprocessing_contract,
            )
            shutil.copy2(source_label, target_label)
            sanitized_items.append(
                {
                    "frame_id": frame_id,
                    "split": split,
                    "image_path": f"images/{split}/{frame_id}.jpg",
                    "label_path": f"labels/{split}/{frame_id}.txt",
                    "image_sha256": sha256_file(target_image),
                    "label_sha256": sha256_file(target_label),
                }
            )
        shutil.copy2(source / "data.yaml", temporary / "data.yaml")
        source_manifest_sha256 = sha256_file(source / "manifest.json")
        original_frames_included = preprocessing_method == ORIGINAL_FRAME
        write_json(
            temporary / "manifest.json",
            {
                "schema_version": 1,
                "artifact_type": (
                    "approved-original-frame-training-dataset"
                    if original_frames_included
                    else "deidentified-colab-dataset"
                ),
                "source_dataset_version": report["dataset_version"],
                "source_manifest_sha256": source_manifest_sha256,
                "class_names": ["person"],
                "raw_video_paths_included": False,
                "source_metadata_included": False,
                "original_frames_included": original_frames_included,
                "items": sanitized_items,
            },
        )
        receipt_path = temporary / "privacy_receipt.json"
        write_json(
            receipt_path,
            {
                "schema_version": 2,
                "exported_at": utc_now_iso(),
                "operator_id": operator_id.strip(),
                "approval_mode": approval_mode,
                "approval_reference": cohort_policy or None,
                "manual_privacy_review_confirmed": manual_privacy_review_confirmed,
                "method": preprocessing_method,
                "head_fraction": head_fraction,
                "preprocessing_contract": preprocessing_contract,
                "training_compatible": preprocessing_contract["training_compatible"],
                "image_count": len(sanitized_items),
                "audio_included": False,
                "raw_video_included": False,
                "absolute_source_paths_included": False,
                "original_frames_included": original_frames_included,
                "source_manifest_sha256": source_manifest_sha256,
            },
        )
        temporary.replace(target)
    return target


def _validate_original_frame_approvals(manifest: dict[str, object]) -> None:
    """원본 이미지 반출 전에 모든 항목의 명시적 학습 승인을 확인한다."""

    items = manifest.get("items")
    if not isinstance(items, list) or not items:
        raise AutoLabelingError("원본 프레임 데이터셋에 승인 항목이 없습니다.")
    now = datetime.now(UTC)
    for item in items:
        if not isinstance(item, dict):
            raise AutoLabelingError("원본 프레임 승인 항목이 객체가 아닙니다.")
        if (
            item.get("approved_student_data") is not True
            or item.get("approval_type") != "human-reviewed"
            or item.get("consent_scope") != "person-detection-training"
            or item.get("subject_category") != "student"
            or not str(item.get("approval_reference", "")).strip()
        ):
            raise AutoLabelingError(
                "원본 프레임에는 사람 검수된 학생 탐지 학습 승인이 필요합니다."
            )
        try:
            retention_expires_at = datetime.fromisoformat(
                str(item.get("retention_expires_at", ""))
            )
        except ValueError as exc:
            raise AutoLabelingError(
                "원본 프레임 보존 기한이 올바르지 않습니다."
            ) from exc
        if (
            retention_expires_at.tzinfo is None
            or retention_expires_at.astimezone(UTC) <= now
        ):
            raise AutoLabelingError(
                "보존 기한이 지난 원본 프레임은 반출할 수 없습니다."
            )


def extend_deidentified_dataset_with_reviewed_validation(
    base_export_dir: Path,
    review_dirs: Sequence[Path],
    output_dir: Path,
    *,
    operator_id: str,
    approved_cohort_policy: str,
    dataset_version: str,
    exclusion_receipt_paths: Sequence[Path] = (),
    head_fraction: float = 0.3,
) -> Path:
    """기존 비식별 train에 검수 완료된 val을 비식별화해 결합한다."""

    if not operator_id.strip():
        raise AutoLabelingError("operator_id가 필요합니다.")
    cohort_policy = approved_cohort_policy.strip()
    if not cohort_policy:
        raise AutoLabelingError("승인된 학생 집단 정책 참조가 필요합니다.")
    if not dataset_version.strip():
        raise AutoLabelingError("결합 데이터셋 버전이 필요합니다.")
    if not 0.2 <= head_fraction <= 0.5:
        raise AutoLabelingError("head_fraction은 0.2~0.5여야 합니다.")
    if not review_dirs:
        raise AutoLabelingError("검수 완료된 val 폴더가 한 개 이상 필요합니다.")

    base_root = base_export_dir.resolve(strict=True)
    base_report = validate_privacy_export(base_root)
    base_preprocessing_contract = base_report.get("preprocessing_contract")
    if not isinstance(base_preprocessing_contract, dict):
        raise AutoLabelingError("기준 Colab export 전처리 계약이 없습니다.")
    base_manifest_path = base_root / "manifest.json"
    base_receipt_path = base_root / "privacy_receipt.json"
    base_manifest = read_json(base_manifest_path)
    if not isinstance(base_manifest, dict):
        raise AutoLabelingError("기준 Colab export manifest가 올바르지 않습니다.")
    base_items = base_manifest.get("items")
    if not isinstance(base_items, list) or not base_items:
        raise AutoLabelingError("기준 Colab export에 항목이 없습니다.")
    if any(
        not isinstance(item, dict) or item.get("split") != "train"
        for item in base_items
    ):
        raise AutoLabelingError("기준 Colab export는 train 전용이어야 합니다.")

    exclusions: dict[tuple[str, str], tuple[Path, dict[str, object]]] = {}
    for raw_path in exclusion_receipt_paths:
        path = raw_path.resolve(strict=True)
        raw = read_json(path)
        if not isinstance(raw, dict):
            raise AutoLabelingError("val 제외 영수증이 올바르지 않습니다.")
        run_id = str(raw.get("run_id", ""))
        batch_id = str(raw.get("batch_id", ""))
        if not run_id or not batch_id:
            raise AutoLabelingError("val 제외 영수증의 실행·배치 ID가 없습니다.")
        key = (run_id, batch_id)
        if key in exclusions:
            raise AutoLabelingError("같은 검수 배치의 제외 영수증이 중복됐습니다.")
        exclusions[key] = (path, raw)

    selected_frames: list[tuple[Path, str]] = []
    review_sources: list[dict[str, object]] = []
    used_exclusions: set[tuple[str, str]] = set()
    selected_ids: set[str] = set()
    for raw_review_dir in review_dirs:
        review_root = raw_review_dir.resolve(strict=True)
        receipt = verify_review_receipt(review_root)
        batch = read_json(review_root / "review-batch.json")
        if not isinstance(batch, dict):
            raise AutoLabelingError("review-batch.json이 올바르지 않습니다.")
        run_id = str(batch.get("run_id", ""))
        batch_id = str(batch.get("batch_id", ""))
        frame_ids = batch.get("frame_ids")
        if not run_id or not batch_id or not isinstance(frame_ids, list):
            raise AutoLabelingError("검수 배치의 실행·프레임 목록이 올바르지 않습니다.")

        key = (run_id, batch_id)
        excluded_ids: set[str] = set()
        exclusion_sha256 = None
        if key in exclusions:
            exclusion_path, exclusion = exclusions[key]
            used_exclusions.add(key)
            if exclusion.get("review_batch_sha256") != sha256_file(
                review_root / "review-batch.json"
            ):
                raise AutoLabelingError("val 제외 영수증의 검수 배치 해시가 다릅니다.")
            raw_excluded = exclusion.get("excluded_frames")
            if not isinstance(raw_excluded, list):
                raise AutoLabelingError("val 제외 프레임 목록이 올바르지 않습니다.")
            for item in raw_excluded:
                if not isinstance(item, dict):
                    raise AutoLabelingError("val 제외 프레임 항목이 올바르지 않습니다.")
                frame_id = str(item.get("frame_id", ""))
                if frame_id not in frame_ids or frame_id in excluded_ids:
                    raise AutoLabelingError("val 제외 frame_id가 올바르지 않습니다.")
                image_path = review_root / f"{frame_id}.jpg"
                label_path = review_root / f"{frame_id}.txt"
                if item.get("image_sha256") != sha256_file(image_path) or item.get(
                    "label_sha256"
                ) != sha256_file(label_path):
                    raise AutoLabelingError(
                        "val 제외 프레임이 영수증 생성 뒤 변경됐습니다."
                    )
                excluded_ids.add(frame_id)
            if exclusion.get("excluded_frame_count") != len(excluded_ids):
                raise AutoLabelingError("val 제외 프레임 수가 영수증과 다릅니다.")
            exclusion_sha256 = sha256_file(exclusion_path)

        retained_count = 0
        for raw_frame_id in frame_ids:
            frame_id = str(raw_frame_id)
            if frame_id in excluded_ids:
                continue
            if not frame_id or frame_id in selected_ids:
                raise AutoLabelingError("결합할 val frame_id가 비었거나 중복됐습니다.")
            selected_ids.add(frame_id)
            selected_frames.append((review_root, frame_id))
            retained_count += 1
        review_sources.append(
            {
                "run_id": run_id,
                "batch_id": batch_id,
                "reviewer_id": receipt.get("reviewer_id"),
                "review_receipt_sha256": sha256_file(
                    review_root / "review-completed.json"
                ),
                "exclusion_receipt_sha256": exclusion_sha256,
                "input_frame_count": len(frame_ids),
                "excluded_frame_count": len(excluded_ids),
                "retained_frame_count": retained_count,
            }
        )
    if set(exclusions) != used_exclusions:
        raise AutoLabelingError("사용되지 않은 val 제외 영수증이 있습니다.")
    if not selected_frames:
        raise AutoLabelingError("결합할 val 프레임이 없습니다.")

    base_frame_ids = {
        str(item.get("frame_id", "")) for item in base_items if isinstance(item, dict)
    }
    if not base_frame_ids.isdisjoint(selected_ids):
        raise AutoLabelingError("기준 train과 추가 val의 frame_id가 중복됩니다.")

    target = output_dir.resolve()
    if target.exists():
        raise AutoLabelingError("결합 Colab export 출력 디렉터리가 이미 있습니다.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{target.name}-", dir=target.parent
    ) as temp:
        temporary = Path(temp)
        combined_items: list[dict[str, object]] = []
        for raw_item in base_items:
            if not isinstance(raw_item, dict):
                raise AutoLabelingError("기준 train 항목이 올바르지 않습니다.")
            frame_id = str(raw_item.get("frame_id", ""))
            source_image = base_root / str(raw_item.get("image_path", ""))
            source_label = base_root / str(raw_item.get("label_path", ""))
            target_image = temporary / "images" / "train" / f"{frame_id}.jpg"
            target_label = temporary / "labels" / "train" / f"{frame_id}.txt"
            target_image.parent.mkdir(parents=True, exist_ok=True)
            target_label.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_image, target_image)
            shutil.copy2(source_label, target_label)
            combined_items.append(
                {
                    "frame_id": frame_id,
                    "split": "train",
                    "image_path": f"images/train/{frame_id}.jpg",
                    "label_path": f"labels/train/{frame_id}.txt",
                    "image_sha256": sha256_file(target_image),
                    "label_sha256": sha256_file(target_label),
                }
            )

        for review_root, frame_id in selected_frames:
            source_image = review_root / f"{frame_id}.jpg"
            source_label = review_root / f"{frame_id}.txt"
            target_image = temporary / "images" / "val" / f"{frame_id}.jpg"
            target_label = temporary / "labels" / "val" / f"{frame_id}.txt"
            target_image.parent.mkdir(parents=True, exist_ok=True)
            target_label.parent.mkdir(parents=True, exist_ok=True)
            _write_deidentified_image(
                source_image,
                source_label,
                target_image,
                frame_id=frame_id,
                head_fraction=head_fraction,
                preprocessing_contract=base_preprocessing_contract,
            )
            shutil.copy2(source_label, target_label)
            combined_items.append(
                {
                    "frame_id": frame_id,
                    "split": "val",
                    "image_path": f"images/val/{frame_id}.jpg",
                    "label_path": f"labels/val/{frame_id}.txt",
                    "image_sha256": sha256_file(target_image),
                    "label_sha256": sha256_file(target_label),
                }
            )

        shutil.copy2(base_root / "data.yaml", temporary / "data.yaml")
        write_json(
            temporary / "manifest.json",
            {
                "schema_version": 2,
                "artifact_type": "deidentified-colab-dataset-with-validation",
                "source_dataset_version": dataset_version.strip(),
                "base_source_dataset_version": base_report.get(
                    "source_dataset_version"
                ),
                "base_export_manifest_sha256": sha256_file(base_manifest_path),
                "base_privacy_receipt_sha256": sha256_file(base_receipt_path),
                "class_names": ["person"],
                "raw_video_paths_included": False,
                "source_metadata_included": False,
                "split_counts": {
                    "train": len(base_items),
                    "val": len(selected_frames),
                },
                "review_sources": review_sources,
                "items": combined_items,
            },
        )
        combined_manifest_sha256 = sha256_file(temporary / "manifest.json")
        write_json(
            temporary / "privacy_receipt.json",
            {
                "schema_version": 2,
                "exported_at": utc_now_iso(),
                "operator_id": operator_id.strip(),
                "approval_mode": "approved-student-cohort-policy",
                "approval_reference": cohort_policy,
                "manual_privacy_review_confirmed": False,
                "method": base_preprocessing_contract.get("method"),
                "head_fraction": head_fraction,
                "preprocessing_contract": base_preprocessing_contract,
                "training_compatible": base_preprocessing_contract.get(
                    "training_compatible"
                ),
                "image_count": len(combined_items),
                "split_counts": {
                    "train": len(base_items),
                    "val": len(selected_frames),
                },
                "audio_included": False,
                "raw_video_included": False,
                "absolute_source_paths_included": False,
                "source_manifest_sha256": combined_manifest_sha256,
            },
        )
        validate_privacy_export(temporary)
        temporary.replace(target)
    return target


def extend_deidentified_dataset_with_reviewed_negatives(
    base_export_dir: Path,
    negative_review_dir: Path,
    output_dir: Path,
    *,
    operator_id: str,
    approved_cohort_policy: str,
    dataset_version: str,
) -> Path:
    """검수된 사람 없는 배경 이미지를 기존 비식별 Train에 추가한다."""

    if not operator_id.strip():
        raise AutoLabelingError("operator_id가 필요합니다.")
    cohort_policy = approved_cohort_policy.strip()
    if not cohort_policy:
        raise AutoLabelingError("승인된 학생 집단 정책 참조가 필요합니다.")
    if not dataset_version.strip():
        raise AutoLabelingError("네거티브 결합 데이터셋 버전이 필요합니다.")

    base_root = base_export_dir.resolve(strict=True)
    base_report = validate_privacy_export(base_root)
    base_contract = base_report.get("preprocessing_contract")
    if not isinstance(base_contract, dict):
        raise AutoLabelingError("기준 Colab export 전처리 계약이 없습니다.")
    base_manifest_path = base_root / "manifest.json"
    base_receipt_path = base_root / "privacy_receipt.json"
    base_manifest = read_json(base_manifest_path)
    if not isinstance(base_manifest, dict):
        raise AutoLabelingError("기준 Colab export manifest가 올바르지 않습니다.")
    base_items = base_manifest.get("items")
    if not isinstance(base_items, list) or not base_items:
        raise AutoLabelingError("기준 Colab export에 항목이 없습니다.")

    review_root = negative_review_dir.resolve(strict=True)
    review_path = review_root / "negative_review.json"
    review = read_json(review_path)
    if not isinstance(review, dict) or review.get("schema_version") != 1:
        raise AutoLabelingError("배경 네거티브 검수 영수증이 올바르지 않습니다.")
    reviewer_id = str(review.get("reviewer_id", "")).strip()
    if not reviewer_id or review.get("manual_visual_review_confirmed") is not True:
        raise AutoLabelingError("배경 네거티브 수동 시각 검수가 확인되지 않았습니다.")
    if review.get("preprocessing_contract") != base_contract:
        raise AutoLabelingError("배경 네거티브 전처리 계약이 기준 데이터와 다릅니다.")
    raw_negative_items = review.get("items")
    if not isinstance(raw_negative_items, list) or not raw_negative_items:
        raise AutoLabelingError("검수된 배경 네거티브가 없습니다.")

    base_frame_ids = {
        str(item.get("frame_id", "")) for item in base_items if isinstance(item, dict)
    }
    negative_items: list[dict[str, object]] = []
    negative_ids: set[str] = set()
    for raw_item in raw_negative_items:
        if not isinstance(raw_item, dict):
            raise AutoLabelingError("배경 네거티브 항목이 객체가 아닙니다.")
        frame_id = str(raw_item.get("frame_id", "")).strip()
        if not frame_id or frame_id in base_frame_ids or frame_id in negative_ids:
            raise AutoLabelingError("배경 네거티브 frame_id가 비었거나 중복됐습니다.")
        if raw_item.get("no_person_confirmed") is not True:
            raise AutoLabelingError(
                "사람 없음이 확인되지 않은 배경 네거티브가 있습니다."
            )
        relative = Path(str(raw_item.get("image_path", "")))
        if relative.is_absolute() or ".." in relative.parts:
            raise AutoLabelingError("배경 네거티브 이미지 경로가 올바르지 않습니다.")
        source_image = review_root / relative
        if not source_image.is_file():
            raise AutoLabelingError("배경 네거티브 이미지가 없습니다.")
        if sha256_file(source_image) != raw_item.get("image_sha256"):
            raise AutoLabelingError("배경 네거티브 이미지 해시가 다릅니다.")
        image = cv2.imread(str(source_image))
        if image is None or image.shape[0] < 32 or image.shape[1] < 32:
            raise AutoLabelingError("배경 네거티브 이미지를 읽을 수 없습니다.")
        negative_ids.add(frame_id)
        negative_items.append(
            {
                "frame_id": frame_id,
                "source_image": source_image,
                "source_video_name": str(raw_item.get("source_video_name", "")),
                "source_time_seconds": raw_item.get("source_time_seconds"),
                "crop_xyxy": raw_item.get("crop_xyxy"),
            }
        )

    target = output_dir.resolve()
    if target.exists():
        raise AutoLabelingError(
            "네거티브 결합 Colab export 출력 디렉터리가 이미 있습니다."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{target.name}-", dir=target.parent
    ) as temp:
        temporary = Path(temp)
        combined_items: list[dict[str, object]] = []
        for raw_item in base_items:
            if not isinstance(raw_item, dict):
                raise AutoLabelingError("기준 데이터 항목이 올바르지 않습니다.")
            frame_id = str(raw_item.get("frame_id", ""))
            split = str(raw_item.get("split", ""))
            if split not in {"train", "val"}:
                raise AutoLabelingError("기준 데이터 split이 올바르지 않습니다.")
            source_image = base_root / str(raw_item.get("image_path", ""))
            source_label = base_root / str(raw_item.get("label_path", ""))
            target_image = temporary / "images" / split / f"{frame_id}.jpg"
            target_label = temporary / "labels" / split / f"{frame_id}.txt"
            target_image.parent.mkdir(parents=True, exist_ok=True)
            target_label.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_image, target_image)
            shutil.copy2(source_label, target_label)
            combined_items.append(
                {
                    "frame_id": frame_id,
                    "split": split,
                    "image_path": f"images/{split}/{frame_id}.jpg",
                    "label_path": f"labels/{split}/{frame_id}.txt",
                    "image_sha256": sha256_file(target_image),
                    "label_sha256": sha256_file(target_label),
                }
            )

        negative_sources: list[dict[str, object]] = []
        for item in negative_items:
            frame_id = str(item["frame_id"])
            target_image = temporary / "images" / "train" / f"{frame_id}.jpg"
            target_label = temporary / "labels" / "train" / f"{frame_id}.txt"
            target_image.parent.mkdir(parents=True, exist_ok=True)
            target_label.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cast(Path, item["source_image"]), target_image)
            target_label.write_text("", encoding="utf-8")
            combined_items.append(
                {
                    "frame_id": frame_id,
                    "split": "train",
                    "image_path": f"images/train/{frame_id}.jpg",
                    "label_path": f"labels/train/{frame_id}.txt",
                    "image_sha256": sha256_file(target_image),
                    "label_sha256": sha256_file(target_label),
                }
            )
            negative_sources.append(
                {
                    "frame_id": frame_id,
                    "source_video_name": item["source_video_name"],
                    "source_time_seconds": item["source_time_seconds"],
                    "crop_xyxy": item["crop_xyxy"],
                }
            )

        shutil.copy2(base_root / "data.yaml", temporary / "data.yaml")
        train_count = sum(item["split"] == "train" for item in combined_items)
        val_count = sum(item["split"] == "val" for item in combined_items)
        write_json(
            temporary / "manifest.json",
            {
                "schema_version": 3,
                "artifact_type": "deidentified-colab-dataset-with-background-negatives",
                "source_dataset_version": dataset_version.strip(),
                "base_source_dataset_version": base_report.get(
                    "source_dataset_version"
                ),
                "base_export_manifest_sha256": sha256_file(base_manifest_path),
                "base_privacy_receipt_sha256": sha256_file(base_receipt_path),
                "negative_review_sha256": sha256_file(review_path),
                "negative_reviewer_id": reviewer_id,
                "class_names": ["person"],
                "raw_video_paths_included": False,
                "source_metadata_included": False,
                "split_counts": {"train": train_count, "val": val_count},
                "negative_count": len(negative_items),
                "negative_sources": negative_sources,
                "items": combined_items,
            },
        )
        combined_manifest_sha256 = sha256_file(temporary / "manifest.json")
        write_json(
            temporary / "privacy_receipt.json",
            {
                "schema_version": 2,
                "exported_at": utc_now_iso(),
                "operator_id": operator_id.strip(),
                "approval_mode": "approved-student-cohort-policy",
                "approval_reference": cohort_policy,
                "manual_privacy_review_confirmed": False,
                "method": base_contract.get("method"),
                "preprocessing_contract": base_contract,
                "training_compatible": base_contract.get("training_compatible"),
                "image_count": len(combined_items),
                "split_counts": {"train": train_count, "val": val_count},
                "negative_count": len(negative_items),
                "negative_review_sha256": sha256_file(review_path),
                "base_export_manifest_sha256": sha256_file(base_manifest_path),
                "audio_included": False,
                "raw_video_included": False,
                "absolute_source_paths_included": False,
                "source_manifest_sha256": combined_manifest_sha256,
            },
        )
        validate_privacy_export(temporary)
        temporary.replace(target)
    return target


def validate_privacy_export(export_dir: Path) -> dict[str, Any]:
    root = export_dir.resolve(strict=True)
    manifest = read_json(root / "manifest.json")
    receipt = read_json(root / "privacy_receipt.json")
    if not isinstance(manifest, dict) or not isinstance(receipt, dict):
        raise AutoLabelingError("비식별화 manifest 또는 영수증이 올바르지 않습니다.")
    approval_mode = _validate_approval_receipt(receipt)
    preprocessing_contract = _receipt_preprocessing_contract(receipt)
    if any(
        receipt.get(field) is not False
        for field in (
            "audio_included",
            "raw_video_included",
            "absolute_source_paths_included",
        )
    ):
        raise AutoLabelingError("privacy receipt의 반출 차단 필드가 올바르지 않습니다.")
    items = manifest.get("items")
    if not isinstance(items, list) or not items:
        raise AutoLabelingError("비식별화 데이터셋에 항목이 없습니다.")
    split_counts = {"train": 0, "val": 0}
    for item in items:
        if not isinstance(item, dict):
            raise AutoLabelingError("비식별화 항목이 객체가 아닙니다.")
        split = item.get("split")
        if split not in split_counts:
            raise AutoLabelingError("비식별화 항목의 split이 올바르지 않습니다.")
        split_counts[split] += 1
        for key in ("image_path", "label_path"):
            relative = Path(str(item.get(key, "")))
            if relative.is_absolute() or ".." in relative.parts:
                raise AutoLabelingError(
                    "비식별화 manifest에 로컬 절대 경로가 있습니다."
                )
            path = root / relative
            if not path.is_file():
                raise AutoLabelingError("비식별화 데이터셋 파일이 없습니다.")
        if sha256_file(root / str(item["image_path"])) != item.get("image_sha256"):
            raise AutoLabelingError("비식별 이미지 해시가 다릅니다.")
        if sha256_file(root / str(item["label_path"])) != item.get("label_sha256"):
            raise AutoLabelingError("비식별 라벨 해시가 다릅니다.")
        parse_yolo_file(root / str(item["label_path"]))
    return {
        "status": "valid",
        "image_count": len(items),
        "split_counts": split_counts,
        "source_dataset_version": manifest.get("source_dataset_version"),
        "approval_mode": approval_mode,
        "preprocessing_contract": preprocessing_contract,
        "training_compatible": preprocessing_contract["training_compatible"],
    }


def _write_deidentified_image(
    source_image: Path,
    source_label: Path,
    target_image: Path,
    *,
    frame_id: str,
    head_fraction: float,
    preprocessing_contract: dict[str, object],
) -> None:
    method = preprocessing_contract.get("method")
    if method == ORIGINAL_FRAME:
        shutil.copy2(source_image, target_image)
        return
    image = cv2.imread(str(source_image))
    if image is None:
        raise AutoLabelingError(f"frame_id={frame_id}: 이미지를 읽을 수 없습니다.")
    if method == UNIFORM_FULL_FRAME_PIXELATION:
        block_size = preprocessing_contract.get("pixelation_block_size")
        if not isinstance(block_size, int):
            raise AutoLabelingError("전체 프레임 픽셀화 블록 크기가 없습니다.")
        image = apply_training_preprocessing(image, preprocessing_contract)
    elif method in {PERSON_BBOX_TOP_PIXELATION, LEGACY_COMBINED_PIXELATION}:
        boxes = parse_yolo_file(source_label)
        height, width = image.shape[:2]
        for box in boxes:
            left, top, right, bottom = box.xyxy
            x1 = max(0, min(width - 1, round(left * width)))
            x2 = max(x1 + 1, min(width, round(right * width)))
            y1 = max(0, min(height - 1, round(top * height)))
            person_bottom = max(y1 + 1, min(height, round(bottom * height)))
            y2 = max(
                y1 + 1,
                min(person_bottom, round(y1 + (person_bottom - y1) * head_fraction)),
            )
            _pixelate(cast(NDArray[np.uint8], image), x1, y1, x2, y2)
    else:
        raise AutoLabelingError("지원하지 않는 개인정보 전처리 계약입니다.")
    if not cv2.imwrite(str(target_image), image, [cv2.IMWRITE_JPEG_QUALITY, 95]):
        raise AutoLabelingError(
            f"frame_id={frame_id}: 비식별 이미지를 저장할 수 없습니다."
        )


def _preprocessing_contract(
    method: str, *, head_fraction: float, pixelation_block_size: int
) -> dict[str, object]:
    if method == UNIFORM_FULL_FRAME_PIXELATION:
        return uniform_pixelation_contract(pixelation_block_size)
    if method == ORIGINAL_FRAME:
        return original_frame_contract()
    if method == PERSON_BBOX_TOP_PIXELATION:
        return {
            "schema_version": 1,
            "method": method,
            "label_derived": True,
            "training_compatible": False,
            "inference_preprocessing_required": False,
            "head_fraction": head_fraction,
        }
    raise AutoLabelingError("지원하지 않는 개인정보 전처리 방식입니다.")


def _receipt_preprocessing_contract(
    receipt: dict[str, object],
) -> dict[str, object]:
    raw_contract = receipt.get("preprocessing_contract")
    if isinstance(raw_contract, dict):
        contract = dict(raw_contract)
    else:
        method = str(receipt.get("method", ""))
        if method not in {PERSON_BBOX_TOP_PIXELATION, LEGACY_COMBINED_PIXELATION}:
            raise AutoLabelingError("privacy receipt 전처리 계약이 없습니다.")
        contract = {
            "schema_version": 1,
            "method": method,
            "label_derived": True,
            "training_compatible": False,
            "inference_preprocessing_required": False,
            "head_fraction": receipt.get("head_fraction"),
        }
    if contract.get("schema_version") != 1:
        raise AutoLabelingError("지원하지 않는 전처리 계약 버전입니다.")
    if contract.get("label_derived") is True:
        if contract.get("training_compatible") is not False:
            raise AutoLabelingError("정답 기반 전처리는 학습 호환일 수 없습니다.")
        return contract
    contract_method = contract.get("method")
    if contract_method == ORIGINAL_FRAME:
        if contract.get("label_derived") is not False:
            raise AutoLabelingError("원본 프레임 계약은 라벨에서 파생될 수 없습니다.")
        if contract.get("training_compatible") is not True:
            raise AutoLabelingError("원본 프레임 계약의 학습 호환 값이 잘못됐습니다.")
        if contract.get("inference_preprocessing_required") is not False:
            raise AutoLabelingError(
                "원본 프레임 계약은 추론 전처리를 요구할 수 없습니다."
            )
        if receipt.get("original_frames_included") is not True:
            raise AutoLabelingError("원본 프레임 반출 사실이 영수증에 없습니다.")
        if receipt.get("approval_mode") != "approved-student-cohort-policy":
            raise AutoLabelingError(
                "원본 프레임 반출은 승인된 학생 집단 정책이 필요합니다."
            )
        return contract
    if contract_method != UNIFORM_FULL_FRAME_PIXELATION:
        raise AutoLabelingError("지원하지 않는 라벨 독립 전처리 방식입니다.")
    if contract.get("training_compatible") is not True:
        raise AutoLabelingError("라벨 독립 전처리의 학습 호환 값이 잘못됐습니다.")
    block_size = contract.get("pixelation_block_size")
    if not isinstance(block_size, int) or not 2 <= block_size <= 32:
        raise AutoLabelingError("전처리 계약의 픽셀화 블록 크기가 올바르지 않습니다.")
    return contract


def _validate_approval_receipt(receipt: dict[str, object]) -> str:
    schema_version = receipt.get("schema_version")
    if schema_version == 1:
        if receipt.get("manual_privacy_review_confirmed") is not True:
            raise AutoLabelingError("수동 개인정보 검토가 확인되지 않았습니다.")
        return "manual-review"
    if schema_version != 2:
        raise AutoLabelingError("지원하지 않는 privacy receipt 버전입니다.")

    approval_mode = receipt.get("approval_mode")
    if approval_mode == "manual-review":
        if receipt.get("manual_privacy_review_confirmed") is not True:
            raise AutoLabelingError("수동 개인정보 검토가 확인되지 않았습니다.")
        return approval_mode
    if approval_mode == "approved-student-cohort-policy":
        if receipt.get("manual_privacy_review_confirmed") is not False:
            raise AutoLabelingError(
                "자동 승인 영수증의 수동 검토 값이 올바르지 않습니다."
            )
        approval_reference = receipt.get("approval_reference")
        if not isinstance(approval_reference, str) or not approval_reference.strip():
            raise AutoLabelingError("자동 승인 정책 참조가 없습니다.")
        return approval_mode
    raise AutoLabelingError("privacy receipt의 승인 방식이 올바르지 않습니다.")


def _pixelate(image: NDArray[np.uint8], x1: int, y1: int, x2: int, y2: int) -> None:
    region = image[y1:y2, x1:x2]
    if region.size == 0:
        return
    small_width = max(1, min(10, (x2 - x1) // 8))
    small_height = max(1, min(10, (y2 - y1) // 8))
    small = cv2.resize(
        region, (small_width, small_height), interpolation=cv2.INTER_AREA
    )
    image[y1:y2, x1:x2] = cv2.resize(
        small, (x2 - x1, y2 - y1), interpolation=cv2.INTER_NEAREST
    )
