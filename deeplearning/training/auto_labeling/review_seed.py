from __future__ import annotations

import csv
import hashlib
import shutil
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .core import (
    frame_id_from_record,
    read_json,
    read_jsonl,
    sha256_file,
    utc_now_iso,
    write_json,
)
from .errors import AutoLabelingError
from .review import verify_review_batch_provenance, verify_review_receipt
from .yolo import YoloBox, iou, parse_yolo_file


def seed_review_from_verified_reviews(
    target_review_dir: Path,
    source_review_dirs: Sequence[Path],
    *,
    spot_check_fraction: float = 0.1,
) -> Path:
    """YOLO11 후보 위에 과거 검증된 수동 라벨을 안전하게 복원한다."""

    if not 0 < spot_check_fraction <= 1:
        raise AutoLabelingError("spot_check_fraction은 0 초과 1 이하여야 합니다.")
    target = target_review_dir.resolve(strict=True)
    if (target / "review-completed.json").exists():
        raise AutoLabelingError("완료된 검수 배치는 다시 시드할 수 없습니다.")
    receipt_path = target / "review-seed-receipt.json"
    if receipt_path.exists():
        _verify_seed_receipt(target, receipt_path)
        return receipt_path

    batch = read_json(target / "review-batch.json")
    if not isinstance(batch, dict) or not isinstance(batch.get("frame_ids"), list):
        raise AutoLabelingError("대상 review-batch.json이 올바르지 않습니다.")
    verify_review_batch_provenance(target, batch=batch)
    target_ids = [str(value) for value in batch["frame_ids"]]
    if len(target_ids) != len(set(target_ids)):
        raise AutoLabelingError("대상 검수 frame_id가 중복됐습니다.")

    source_by_frame: dict[str, Path] = {}
    source_receipts: list[dict[str, object]] = []
    for raw_source_dir in source_review_dirs:
        source = raw_source_dir.resolve(strict=True)
        receipt = verify_review_receipt(source)
        raw_files = receipt.get("files")
        if not isinstance(raw_files, list):
            raise AutoLabelingError("과거 검수 영수증의 files가 올바르지 않습니다.")
        source_frame_count = 0
        for item in raw_files:
            if not isinstance(item, dict):
                raise AutoLabelingError("과거 검수 파일 항목이 올바르지 않습니다.")
            frame_id = str(item.get("frame_id", ""))
            if frame_id not in target_ids:
                continue
            if frame_id in source_by_frame:
                raise AutoLabelingError(
                    "같은 frame_id의 과거 검수 라벨이 중복됐습니다."
                )
            source_by_frame[frame_id] = source
            source_frame_count += 1
        source_receipts.append(
            {
                "run_id": receipt.get("run_id"),
                "batch_id": receipt.get("batch_id"),
                "reviewer_id": receipt.get("reviewer_id"),
                "review_completed_sha256": sha256_file(
                    source / "review-completed.json"
                ),
                "selected_frame_count": source_frame_count,
            }
        )
    missing = sorted(set(target_ids) - set(source_by_frame))
    if missing:
        raise AutoLabelingError(f"과거 검수 라벨이 없는 pilot 프레임입니다: {missing}")

    run_dir = target.parent.parent
    candidate_dir = run_dir / "candidate-labels"
    frames = read_jsonl(run_dir / "frames.jsonl")
    session_by_frame = {
        frame_id_from_record(frame): str(frame.get("session_id", ""))
        for frame in frames
    }
    audit_reasons: dict[str, set[str]] = defaultdict(set)
    file_records: list[dict[str, object]] = []
    unchanged_count = 0
    candidate_box_count = 0
    reviewed_box_count = 0

    for frame_id in target_ids:
        source = source_by_frame[frame_id]
        target_image = target / f"{frame_id}.jpg"
        source_image = source / f"{frame_id}.jpg"
        if sha256_file(target_image) != sha256_file(source_image):
            raise AutoLabelingError(
                f"frame_id={frame_id}: 과거 검수 이미지와 pilot 이미지가 다릅니다."
            )
        candidate_label = candidate_dir / f"{frame_id}.txt"
        current_label = target / f"{frame_id}.txt"
        reviewed_label = source / f"{frame_id}.txt"
        candidate_hash = sha256_file(candidate_label)
        current_hash = sha256_file(current_label)
        reviewed_hash = sha256_file(reviewed_label)
        if current_hash not in {candidate_hash, reviewed_hash}:
            raise AutoLabelingError(
                f"frame_id={frame_id}: 이미 새 수동 수정이 있어 시드할 수 없습니다."
            )

        candidate_boxes = parse_yolo_file(candidate_label)
        reviewed_boxes = parse_yolo_file(reviewed_label)
        candidate_box_count += len(candidate_boxes)
        reviewed_box_count += len(reviewed_boxes)
        if candidate_hash == reviewed_hash:
            unchanged_count += 1
        _collect_geometry_audit_reasons(
            frame_id,
            candidate_boxes,
            reviewed_boxes,
            audit_reasons,
        )
        file_records.append(
            {
                "frame_id": frame_id,
                "source_review_batch": source.name,
                "candidate_label_sha256": candidate_hash,
                "seeded_label_sha256": reviewed_hash,
                "candidate_box_count": len(candidate_boxes),
                "seeded_box_count": len(reviewed_boxes),
            }
        )

    for session_id in sorted(set(session_by_frame.values())):
        session_ids = sorted(
            (
                frame_id
                for frame_id in target_ids
                if session_by_frame.get(frame_id) == session_id
            ),
            key=lambda value: hashlib.sha256(value.encode()).hexdigest(),
        )
        sample_count = max(1, round(len(session_ids) * spot_check_fraction))
        for frame_id in session_ids[:sample_count]:
            audit_reasons[frame_id].add("session-stratified-spot-check")

    # 모든 검증이 끝난 뒤에만 대상 라벨을 과거 수동 검수본으로 교체한다.
    for frame_id in target_ids:
        shutil.copy2(
            source_by_frame[frame_id] / f"{frame_id}.txt", target / f"{frame_id}.txt"
        )

    manual_audit: list[dict[str, Any]] = [
        {
            "frame_id": frame_id,
            "session_id": session_by_frame.get(frame_id),
            "reasons": sorted(reasons),
        }
        for frame_id, reasons in sorted(audit_reasons.items())
    ]
    audit_list_path = target / "manual-audit.csv"
    with audit_list_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("frame_id", "session_id", "reasons"))
        for item in manual_audit:
            writer.writerow(
                (
                    item["frame_id"],
                    item["session_id"],
                    "|".join(item["reasons"]),
                )
            )
    for record in file_records:
        frame_id = str(record["frame_id"])
        record["target_label_sha256"] = sha256_file(target / f"{frame_id}.txt")
    write_json(
        receipt_path,
        {
            "schema_version": 1,
            "run_id": batch.get("run_id"),
            "batch_id": batch.get("batch_id"),
            "seed_method": "verified-prior-human-review-over-yolo11-candidates-v1",
            "review_batch_sha256": sha256_file(target / "review-batch.json"),
            "source_reviews": source_receipts,
            "frame_count": len(target_ids),
            "candidate_box_count": candidate_box_count,
            "seeded_box_count": reviewed_box_count,
            "unchanged_label_count": unchanged_count,
            "changed_label_count": len(target_ids) - unchanged_count,
            "manual_audit_frame_count": len(manual_audit),
            "manual_audit_frames": manual_audit,
            "manual_audit_list": audit_list_path.name,
            "manual_audit_list_sha256": sha256_file(audit_list_path),
            "files": file_records,
            "created_at": utc_now_iso(),
        },
    )
    return receipt_path


def prepare_manual_audit_subset(review_dir: Path) -> Path:
    """집중 검수 대상만 담은 별도 LabelImg 폴더를 만든다."""

    target = review_dir.resolve(strict=True)
    if (target / "review-completed.json").exists():
        raise AutoLabelingError(
            "완료된 검수 배치에는 집중 검수 폴더를 만들 수 없습니다."
        )
    seed_receipt_path = target / "review-seed-receipt.json"
    _verify_seed_receipt(target, seed_receipt_path)
    receipt = read_json(seed_receipt_path)
    raw_audit = receipt.get("manual_audit_frames")
    if not isinstance(raw_audit, list) or not raw_audit:
        raise AutoLabelingError("집중 검수 대상 프레임이 없습니다.")
    frame_ids = [
        str(item.get("frame_id", "")) for item in raw_audit if isinstance(item, dict)
    ]
    if len(frame_ids) != len(raw_audit) or len(frame_ids) != len(set(frame_ids)):
        raise AutoLabelingError("집중 검수 frame_id가 올바르지 않습니다.")

    audit_dir = target / "manual-audit"
    if audit_dir.exists():
        _verify_manual_audit_subset(target, audit_dir, frame_ids)
        return audit_dir

    audit_dir.mkdir()
    try:
        for file_name in ("classes.txt", "predefined_classes.txt"):
            shutil.copy2(target / file_name, audit_dir / file_name)
        for frame_id in frame_ids:
            shutil.copy2(target / f"{frame_id}.jpg", audit_dir / f"{frame_id}.jpg")
            shutil.copy2(target / f"{frame_id}.txt", audit_dir / f"{frame_id}.txt")
        write_json(
            audit_dir / "manual-audit-batch.json",
            {
                "schema_version": 1,
                "run_id": receipt.get("run_id"),
                "batch_id": receipt.get("batch_id"),
                "review_seed_receipt_sha256": sha256_file(seed_receipt_path),
                "frame_count": len(frame_ids),
                "frame_ids": frame_ids,
                "prepared_at": utc_now_iso(),
            },
        )
        _verify_manual_audit_subset(target, audit_dir, frame_ids)
    except Exception:
        if audit_dir.exists():
            shutil.rmtree(audit_dir)
        raise
    return audit_dir


def merge_manual_audit_subset(review_dir: Path) -> Path:
    """집중 검수 폴더의 라벨을 전체 검수 배치에 검증 후 병합한다."""

    target = review_dir.resolve(strict=True)
    if (target / "review-completed.json").exists():
        raise AutoLabelingError("완료된 검수 배치에는 집중 검수를 병합할 수 없습니다.")
    seed_receipt_path = target / "review-seed-receipt.json"
    _verify_seed_receipt(target, seed_receipt_path)
    receipt = read_json(seed_receipt_path)
    raw_audit = receipt.get("manual_audit_frames")
    if not isinstance(raw_audit, list) or not raw_audit:
        raise AutoLabelingError("병합할 집중 검수 대상이 없습니다.")
    frame_ids = [str(item["frame_id"]) for item in raw_audit if isinstance(item, dict)]
    if len(frame_ids) != len(raw_audit):
        raise AutoLabelingError("집중 검수 frame_id가 올바르지 않습니다.")

    audit_dir = target / "manual-audit"
    _verify_manual_audit_subset(target, audit_dir, frame_ids)
    label_records: list[dict[str, object]] = []
    for frame_id in frame_ids:
        label_path = audit_dir / f"{frame_id}.txt"
        boxes = parse_yolo_file(label_path)
        label_records.append(
            {
                "frame_id": frame_id,
                "box_count": len(boxes),
                "audit_label_sha256": sha256_file(label_path),
            }
        )

    for frame_id in frame_ids:
        shutil.copy2(audit_dir / f"{frame_id}.txt", target / f"{frame_id}.txt")
    for record in label_records:
        frame_id = str(record["frame_id"])
        record["merged_label_sha256"] = sha256_file(target / f"{frame_id}.txt")

    merge_receipt_path = target / "manual-audit-merged.json"
    write_json(
        merge_receipt_path,
        {
            "schema_version": 1,
            "run_id": receipt.get("run_id"),
            "batch_id": receipt.get("batch_id"),
            "manual_audit_batch_sha256": sha256_file(
                audit_dir / "manual-audit-batch.json"
            ),
            "frame_count": len(frame_ids),
            "files": label_records,
            "merged_at": utc_now_iso(),
        },
    )
    return merge_receipt_path


def migrate_manual_audit_labels(
    target_review_dir: Path,
    source_review_dir: Path,
    *,
    expected_excluded_frame_ids: Sequence[str] = (),
) -> Path:
    """교체 전 pilot의 완료된 집중 검수 라벨을 새 pilot에 이관한다."""

    target = target_review_dir.resolve(strict=True)
    source = source_review_dir.resolve(strict=True)
    if (target / "review-completed.json").exists():
        raise AutoLabelingError("완료된 대상 검수에는 라벨을 이관할 수 없습니다.")
    target_batch = read_json(target / "review-batch.json")
    source_batch = read_json(source / "review-batch.json")
    if not isinstance(target_batch, dict) or not isinstance(source_batch, dict):
        raise AutoLabelingError("수동 검수 이관 batch가 올바르지 않습니다.")
    verify_review_batch_provenance(target, batch=target_batch)
    verify_review_batch_provenance(source, batch=source_batch)
    target_ids = {str(value) for value in target_batch.get("frame_ids", [])}
    if not target_ids:
        raise AutoLabelingError("대상 검수 frame_id가 없습니다.")

    source_merge_path = source / "manual-audit-merged.json"
    source_merge = read_json(source_merge_path)
    raw_files = source_merge.get("files") if isinstance(source_merge, dict) else None
    if not isinstance(raw_files, list) or not raw_files:
        raise AutoLabelingError("원본 집중 검수 병합 영수증이 올바르지 않습니다.")
    source_records: dict[str, dict[str, object]] = {}
    for raw_record in raw_files:
        if not isinstance(raw_record, dict):
            raise AutoLabelingError("원본 집중 검수 라벨 항목이 올바르지 않습니다.")
        frame_id = str(raw_record.get("frame_id", ""))
        if not frame_id or frame_id in source_records:
            raise AutoLabelingError("원본 집중 검수 frame_id가 올바르지 않습니다.")
        source_label = source / f"{frame_id}.txt"
        if raw_record.get("merged_label_sha256") != sha256_file(source_label):
            raise AutoLabelingError("원본 집중 검수 병합 뒤 라벨이 변경됐습니다.")
        parse_yolo_file(source_label)
        source_records[frame_id] = raw_record

    expected_excluded = {str(value) for value in expected_excluded_frame_ids}
    actual_excluded = set(source_records) - target_ids
    if actual_excluded != expected_excluded:
        raise AutoLabelingError("집중 검수 이관에서 제외된 frame_id가 예상과 다릅니다.")
    migration_ids = sorted(set(source_records) & target_ids)
    if not migration_ids:
        raise AutoLabelingError("새 pilot에 이관할 집중 검수 라벨이 없습니다.")

    migration_records: list[dict[str, object]] = []
    for frame_id in migration_ids:
        source_label = source / f"{frame_id}.txt"
        shutil.copy2(source_label, target / f"{frame_id}.txt")
        migration_records.append(
            {
                "frame_id": frame_id,
                "source_label_sha256": sha256_file(source_label),
                "target_label_sha256": sha256_file(target / f"{frame_id}.txt"),
            }
        )

    receipt_path = target / "manual-audit-migration.json"
    write_json(
        receipt_path,
        {
            "schema_version": 1,
            "source_run_id": source_batch.get("run_id"),
            "source_batch_id": source_batch.get("batch_id"),
            "source_merge_receipt_sha256": sha256_file(source_merge_path),
            "target_run_id": target_batch.get("run_id"),
            "target_batch_id": target_batch.get("batch_id"),
            "migrated_frame_count": len(migration_records),
            "excluded_frame_ids": sorted(actual_excluded),
            "files": migration_records,
            "migrated_at": utc_now_iso(),
        },
    )
    return receipt_path


def _collect_geometry_audit_reasons(
    frame_id: str,
    candidates: list[YoloBox],
    reviewed: list[YoloBox],
    reasons: dict[str, set[str]],
) -> None:
    unmatched = set(range(len(candidates)))
    for reviewed_box in reviewed:
        matches = [(iou(reviewed_box, candidates[index]), index) for index in unmatched]
        if not matches:
            continue
        best_iou, best_index = max(matches)
        if best_iou < 0.1:
            continue
        unmatched.remove(best_index)
        candidate = candidates[best_index]
        candidate_area = candidate.width * candidate.height
        reviewed_area = reviewed_box.width * reviewed_box.height
        if candidate_area > 0 and reviewed_area / candidate_area > 1.5:
            reasons[frame_id].add("reviewed-box-area-much-larger-than-yolo11")
        if candidate.height > 0 and reviewed_box.height / candidate.height > 1.4:
            reviewed_bottom = reviewed_box.center_y + reviewed_box.height / 2
            candidate_bottom = candidate.center_y + candidate.height / 2
            if reviewed_bottom - candidate_bottom > 0.04:
                reasons[frame_id].add("reviewed-box-extends-far-below-yolo11")
        if best_iou < 0.5:
            reasons[frame_id].add("reviewed-box-low-iou-with-yolo11")


def _verify_seed_receipt(target: Path, receipt_path: Path) -> None:
    receipt = read_json(receipt_path)
    if not isinstance(receipt, dict) or not isinstance(receipt.get("files"), list):
        raise AutoLabelingError("검수 시드 영수증이 올바르지 않습니다.")
    if receipt.get("review_batch_sha256") != sha256_file(target / "review-batch.json"):
        raise AutoLabelingError("검수 시드 뒤 review-batch가 변경됐습니다.")
    audit_list_name = str(receipt.get("manual_audit_list", ""))
    if audit_list_name != "manual-audit.csv":
        raise AutoLabelingError("검수 시드 수동 점검 목록 경로가 올바르지 않습니다.")
    if receipt.get("manual_audit_list_sha256") != sha256_file(target / audit_list_name):
        raise AutoLabelingError("검수 시드 뒤 수동 점검 목록이 변경됐습니다.")
    for item in receipt["files"]:
        if not isinstance(item, dict):
            raise AutoLabelingError("검수 시드 파일 항목이 올바르지 않습니다.")
        frame_id = str(item.get("frame_id", ""))
        if item.get("target_label_sha256") != sha256_file(target / f"{frame_id}.txt"):
            raise AutoLabelingError("검수 시드 뒤 라벨이 변경됐습니다.")


def _verify_manual_audit_subset(
    review_dir: Path,
    audit_dir: Path,
    frame_ids: list[str],
) -> None:
    if not audit_dir.is_dir():
        raise AutoLabelingError("집중 검수 폴더가 없습니다.")
    batch = read_json(audit_dir / "manual-audit-batch.json")
    if not isinstance(batch, dict) or batch.get("frame_ids") != frame_ids:
        raise AutoLabelingError("집중 검수 batch가 올바르지 않습니다.")
    if batch.get("review_seed_receipt_sha256") != sha256_file(
        review_dir / "review-seed-receipt.json"
    ):
        raise AutoLabelingError("집중 검수의 seed 영수증이 변경됐습니다.")

    expected = set(frame_ids)
    actual_images = {path.stem for path in audit_dir.glob("*.jpg")}
    actual_labels = {
        path.stem
        for path in audit_dir.glob("*.txt")
        if path.name not in {"classes.txt", "predefined_classes.txt"}
    }
    if actual_images != expected or actual_labels != expected:
        raise AutoLabelingError("집중 검수 이미지·라벨 파일 집합이 다릅니다.")
    for file_name in ("classes.txt", "predefined_classes.txt"):
        _validate_person_class_file(review_dir / file_name)
        _validate_person_class_file(audit_dir / file_name)
    for frame_id in frame_ids:
        if sha256_file(audit_dir / f"{frame_id}.jpg") != sha256_file(
            review_dir / f"{frame_id}.jpg"
        ):
            raise AutoLabelingError("집중 검수 이미지가 전체 검수 이미지와 다릅니다.")


def _validate_person_class_file(path: Path) -> None:
    try:
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, UnicodeError) as exc:
        raise AutoLabelingError("집중 검수 클래스 파일을 읽을 수 없습니다.") from exc
    if [line for line in lines if line] != ["person"]:
        raise AutoLabelingError("집중 검수 클래스 파일은 person 한 줄이어야 합니다.")
