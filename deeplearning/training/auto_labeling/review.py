from __future__ import annotations

import hashlib
import json
import math
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from .core import (
    SAFE_ID_PATTERN,
    Settings,
    frame_id_from_record,
    read_json,
    read_jsonl,
    sha256_file,
    utc_now_iso,
    verified_frame_image_path,
    write_json,
)
from .errors import AutoLabelingError
from .prelabel import candidate_labels_fingerprint, verify_prelabel_artifacts
from .yolo import YoloBox, iou, parse_yolo_file, touches_boundary, validate_yolo_box

CLASS_FILE_CONTENT = "person\n"


def prepare_review(
    run_dir: Path,
    settings: Settings,
    *,
    batch_id: str = "review-main",
    calibration_paths: tuple[Path, ...] = (),
    force_full: bool = False,
) -> Path:
    run_dir = run_dir.resolve(strict=True)
    if not SAFE_ID_PATTERN.fullmatch(batch_id):
        raise AutoLabelingError("batch_id 형식이 올바르지 않습니다.")
    run_manifest = _require_dict(read_json(run_dir / "run.json"), "run.json")
    prelabel_manifest = _require_dict(
        read_json(run_dir / "prelabel.json"), "prelabel.json"
    )
    frames = read_jsonl(run_dir / "frames.jsonl")
    predictions = read_jsonl(run_dir / "predictions.jsonl")
    verify_prelabel_artifacts(run_dir, manifest=prelabel_manifest, frames=frames)
    prediction_by_frame = _index_by_frame_id(predictions, "predictions.jsonl")
    if len(frames) != len(predictions):
        raise AutoLabelingError("프레임과 모델 예측 수가 다릅니다.")
    calibrations = _load_calibrations(
        calibration_paths,
        model_sha256=_model_sha256(prelabel_manifest),
        sampling_policy_version=str(run_manifest.get("sampling_policy_version", "")),
    )
    selected, required, sampled, auto_accepted = _select_review_frames(
        frames,
        prediction_by_frame,
        settings,
        calibrations,
        run_id=str(run_manifest.get("run_id", "")),
        force_full=force_full,
    )
    review_root = run_dir / "review"
    review_root.mkdir(exist_ok=True)
    review_dir = review_root / batch_id
    artifact_provenance: dict[str, object] = {
        "run_sha256": sha256_file(run_dir / "run.json"),
        "frames_sha256": sha256_file(run_dir / "frames.jsonl"),
        "prelabel_sha256": sha256_file(run_dir / "prelabel.json"),
        "predictions_sha256": sha256_file(run_dir / "predictions.jsonl"),
        "candidate_labels_fingerprint": candidate_labels_fingerprint(
            run_dir / "candidate-labels", (_frame_id(frame) for frame in frames)
        ),
    }
    calibration_records = [
        {"file_name": path.name, "sha256": sha256_file(path.resolve(strict=True))}
        for path in calibration_paths
    ]
    selection_fingerprint = _selection_fingerprint(
        selected,
        required,
        sampled,
        auto_accepted,
        force_full=force_full,
        provenance=artifact_provenance,
        calibrations=calibration_records,
    )
    if review_dir.exists():
        batch = _require_dict(
            read_json(review_dir / "review-batch.json"), "review-batch.json"
        )
        if batch.get("selection_fingerprint") != selection_fingerprint:
            raise AutoLabelingError(
                "같은 batch_id가 다른 검수 대상에 이미 사용됐습니다."
            )
        verify_review_batch_provenance(review_dir, batch=batch)
        return review_dir

    frame_by_id = _index_by_frame_id(frames, "frames.jsonl")
    with tempfile.TemporaryDirectory(
        prefix=f".{batch_id}-", dir=review_root
    ) as temporary:
        temporary_dir = Path(temporary)
        for frame_id in selected:
            frame = frame_by_id[frame_id]
            image_path = verified_frame_image_path(run_dir, frame)
            label_path = run_dir / "candidate-labels" / f"{frame_id}.txt"
            if not image_path.is_file() or not label_path.is_file():
                raise AutoLabelingError(f"frame_id={frame_id}: 검수 원본이 없습니다.")
            shutil.copy2(image_path, temporary_dir / f"{frame_id}.jpg")
            shutil.copy2(label_path, temporary_dir / f"{frame_id}.txt")
        (temporary_dir / "classes.txt").write_text(
            CLASS_FILE_CONTENT, encoding="utf-8", newline="\n"
        )
        (temporary_dir / "predefined_classes.txt").write_text(
            CLASS_FILE_CONTENT, encoding="utf-8", newline="\n"
        )
        write_json(
            temporary_dir / "review-batch.json",
            {
                "schema_version": 1,
                "run_id": run_manifest.get("run_id"),
                "batch_id": batch_id,
                "force_full": force_full,
                "frame_ids": selected,
                "required_frame_ids": required,
                "sampled_high_confidence_frame_ids": sampled,
                "auto_accepted_frame_ids": auto_accepted,
                "selection_fingerprint": selection_fingerprint,
                "provenance": artifact_provenance,
                "calibrations": calibration_records,
                "input_image_sha256": {
                    frame_id: sha256_file(temporary_dir / f"{frame_id}.jpg")
                    for frame_id in selected
                },
                "created_at": utc_now_iso(),
            },
        )
        temporary_dir.replace(review_dir)
    return review_dir


def complete_review(
    review_dir: Path,
    reviewer_id: str,
    settings: Settings,
    *,
    labelimg_executable: Path,
    labelimg_smoke_confirmed: bool,
) -> Path:
    review_dir = review_dir.resolve(strict=True)
    if not SAFE_ID_PATTERN.fullmatch(reviewer_id):
        raise AutoLabelingError("reviewer_id 형식이 올바르지 않습니다.")
    if not labelimg_smoke_confirmed:
        raise AutoLabelingError("labelImg 호환성 smoke test 확인이 필요합니다.")
    try:
        resolved_labelimg = labelimg_executable.resolve(strict=True)
    except OSError as exc:
        raise AutoLabelingError("labelImg 실행 파일을 찾을 수 없습니다.") from exc
    if not resolved_labelimg.is_file():
        raise AutoLabelingError("labelImg 실행 경로는 파일이어야 합니다.")
    batch = _require_dict(
        read_json(review_dir / "review-batch.json"), "review-batch.json"
    )
    frame_ids = _string_list(batch.get("frame_ids"), "frame_ids")
    verify_review_batch_provenance(review_dir, batch=batch)
    _validate_class_files(review_dir)
    _validate_review_file_set(review_dir, frame_ids)
    _validate_review_images(review_dir, batch, frame_ids)
    files: list[dict[str, object]] = []
    for frame_id in frame_ids:
        image_path = review_dir / f"{frame_id}.jpg"
        label_path = review_dir / f"{frame_id}.txt"
        if image_path.is_symlink() or label_path.is_symlink():
            raise AutoLabelingError("검수 파일에는 심볼릭 링크를 사용할 수 없습니다.")
        boxes = parse_yolo_file(label_path)
        files.append(
            {
                "frame_id": frame_id,
                "image_sha256": sha256_file(image_path),
                "label_sha256": sha256_file(label_path),
                "label_count": len(boxes),
            }
        )
    quality = _evaluate_sample_quality(review_dir, batch, settings)
    if quality.get("passed") is not True:
        batch_id = str(batch.get("batch_id", "review"))
        batch_hash = hashlib.sha256(batch_id.encode()).hexdigest()[:8]
        fallback_batch_id = f"{batch_id[:100]}-fallback-{batch_hash}"
        fallback_dir = prepare_review(
            review_dir.parent.parent,
            settings,
            batch_id=fallback_batch_id,
            force_full=True,
        )
        quality["fallback_batch_id"] = fallback_dir.name
    receipt = {
        "schema_version": 1,
        "run_id": batch.get("run_id"),
        "batch_id": batch.get("batch_id"),
        "reviewer_id": reviewer_id,
        "completed_at": utc_now_iso(),
        "review_batch_sha256": sha256_file(review_dir / "review-batch.json"),
        "classes_sha256": sha256_file(review_dir / "classes.txt"),
        "predefined_classes_sha256": sha256_file(review_dir / "predefined_classes.txt"),
        "quality_gate": quality,
        "labelimg": {
            "executable_name": resolved_labelimg.name,
            "executable_sha256": sha256_file(resolved_labelimg),
            "smoke_test_confirmed": True,
        },
        "files": files,
    }
    receipt_path = review_dir / "review-completed.json"
    if receipt_path.exists():
        existing = _require_dict(read_json(receipt_path), "review-completed.json")
        _verify_receipt_payload(review_dir, batch, existing)
        return receipt_path
    write_json(receipt_path, receipt)
    return receipt_path


def verify_review_receipt(review_dir: Path) -> dict[str, Any]:
    batch = _require_dict(
        read_json(review_dir / "review-batch.json"), "review-batch.json"
    )
    verify_review_batch_provenance(review_dir, batch=batch)
    receipt_path = review_dir / "review-completed.json"
    if not receipt_path.is_file():
        raise AutoLabelingError(f"{review_dir.name}: 검수 완료 기록이 없습니다.")
    receipt = _require_dict(read_json(receipt_path), "review-completed.json")
    _verify_receipt_payload(review_dir, batch, receipt)
    return receipt


def verify_review_batch_provenance(
    review_dir: Path, *, batch: dict[str, Any] | None = None
) -> dict[str, Any]:
    batch_manifest = batch or _require_dict(
        read_json(review_dir / "review-batch.json"), "review-batch.json"
    )
    run_dir = review_dir.parent.parent
    provenance = _require_dict(batch_manifest.get("provenance"), "provenance")
    expected_files = {
        "run_sha256": run_dir / "run.json",
        "frames_sha256": run_dir / "frames.jsonl",
        "prelabel_sha256": run_dir / "prelabel.json",
        "predictions_sha256": run_dir / "predictions.jsonl",
    }
    for field_name, path in expected_files.items():
        if provenance.get(field_name) != sha256_file(path):
            raise AutoLabelingError(f"검수 배치 생성 뒤 {path.name}이 변경됐습니다.")
    frames = read_jsonl(run_dir / "frames.jsonl")
    verify_prelabel_artifacts(run_dir, frames=frames)
    all_frame_ids = {_frame_id(frame) for frame in frames}
    fingerprint = candidate_labels_fingerprint(
        run_dir / "candidate-labels", (_frame_id(frame) for frame in frames)
    )
    if provenance.get("candidate_labels_fingerprint") != fingerprint:
        raise AutoLabelingError("검수 배치 생성 뒤 후보 라벨이 변경됐습니다.")
    run_manifest = _require_dict(read_json(run_dir / "run.json"), "run.json")
    if batch_manifest.get("run_id") != run_manifest.get("run_id"):
        raise AutoLabelingError("검수 배치의 run_id가 실행과 다릅니다.")
    batch_id = batch_manifest.get("batch_id")
    if (
        not isinstance(batch_id, str)
        or not SAFE_ID_PATTERN.fullmatch(batch_id)
        or batch_id != review_dir.name
    ):
        raise AutoLabelingError("검수 배치 ID와 디렉터리 이름이 다릅니다.")
    selected = _string_list(batch_manifest.get("frame_ids"), "frame_ids")
    required = _string_list(
        batch_manifest.get("required_frame_ids"), "required_frame_ids"
    )
    sampled = _string_list(
        batch_manifest.get("sampled_high_confidence_frame_ids"),
        "sampled_high_confidence_frame_ids",
    )
    auto_accepted = _string_list(
        batch_manifest.get("auto_accepted_frame_ids"), "auto_accepted_frame_ids"
    )
    selected_set = set(selected)
    required_set = set(required)
    sampled_set = set(sampled)
    auto_accepted_set = set(auto_accepted)
    if (
        required_set & sampled_set
        or selected_set != required_set | sampled_set
        or selected_set & auto_accepted_set
        or selected_set | auto_accepted_set != all_frame_ids
    ):
        raise AutoLabelingError("검수·표본·자동 승인 프레임 구성이 올바르지 않습니다.")
    force_full = batch_manifest.get("force_full")
    if not isinstance(force_full, bool):
        raise AutoLabelingError("검수 배치의 force_full 값이 올바르지 않습니다.")
    calibrations = _calibration_records_from_batch(batch_manifest.get("calibrations"))
    expected_selection_fingerprint = _selection_fingerprint(
        selected,
        required,
        sampled,
        auto_accepted,
        force_full=force_full,
        provenance=provenance,
        calibrations=calibrations,
    )
    if batch_manifest.get("selection_fingerprint") != expected_selection_fingerprint:
        raise AutoLabelingError("검수 대상 선택 기록이 변경됐습니다.")
    return batch_manifest


def create_calibration(
    run_dir: Path,
    review_dir: Path,
    settings: Settings,
    *,
    output_path: Path | None = None,
) -> Path:
    run_dir = run_dir.resolve(strict=True)
    review_dir = review_dir.resolve(strict=True)
    if review_dir.parent.parent != run_dir:
        raise AutoLabelingError("검수 배치가 지정한 실행 디렉터리에 속하지 않습니다.")
    receipt = verify_review_receipt(review_dir)
    batch = _require_dict(
        read_json(review_dir / "review-batch.json"), "review-batch.json"
    )
    frames = read_jsonl(run_dir / "frames.jsonl")
    all_frame_ids = {_frame_id(frame) for frame in frames}
    reviewed_frame_ids = set(_string_list(batch.get("frame_ids"), "frame_ids"))
    if reviewed_frame_ids != all_frame_ids:
        raise AutoLabelingError("보정에는 실행의 모든 프레임을 전수 검수해야 합니다.")
    prelabel = _require_dict(read_json(run_dir / "prelabel.json"), "prelabel.json")
    predictions = _index_by_frame_id(
        read_jsonl(run_dir / "predictions.jsonl"), "predictions.jsonl"
    )
    frames_by_camera: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for frame in frames:
        camera_id = frame.get("camera_id")
        if not isinstance(camera_id, str):
            raise AutoLabelingError("frames.jsonl에 camera_id가 없습니다.")
        frames_by_camera[camera_id].append(frame)
    camera_results: dict[str, object] = {}
    for camera_id, camera_frames in sorted(frames_by_camera.items()):
        camera_results[camera_id] = _calibrate_camera(
            camera_frames, predictions, review_dir, settings
        )
    run_manifest = _require_dict(read_json(run_dir / "run.json"), "run.json")
    calibration_path = output_path or run_dir / "calibration.json"
    write_json(
        calibration_path,
        {
            "schema_version": 1,
            "run_id": run_manifest.get("run_id"),
            "model_sha256": _model_sha256(prelabel),
            "sampling_policy_version": run_manifest.get("sampling_policy_version"),
            "review_receipt_sha256": sha256_file(review_dir / "review-completed.json"),
            "reviewer_id": receipt.get("reviewer_id"),
            "created_at": utc_now_iso(),
            "cameras": camera_results,
        },
    )
    return calibration_path


def _select_review_frames(
    frames: list[dict[str, Any]],
    predictions: dict[str, dict[str, Any]],
    settings: Settings,
    calibrations: dict[str, float],
    *,
    run_id: str,
    force_full: bool,
) -> tuple[list[str], list[str], list[str], list[str]]:
    all_frame_ids = sorted(_frame_id(frame) for frame in frames)
    if force_full or not calibrations:
        return all_frame_ids, all_frame_ids, [], []
    required: list[str] = []
    high_confidence: list[dict[str, Any]] = []
    for frame in frames:
        frame_id = _frame_id(frame)
        camera_id = frame.get("camera_id")
        threshold = calibrations.get(str(camera_id))
        prediction = predictions.get(frame_id)
        if threshold is None or prediction is None:
            required.append(frame_id)
            continue
        candidates = _candidate_rows(prediction)
        if _requires_review(candidates, threshold, settings):
            required.append(frame_id)
        else:
            high_confidence.append(frame)
    sample_count = min(
        len(high_confidence),
        max(
            settings.review_sample_min_frames,
            math.ceil(len(high_confidence) * settings.review_sample_fraction),
        ),
    )
    sampled_frames = _stratified_sample(
        high_confidence,
        sample_count,
        run_id=run_id,
        bucket_seconds=settings.review_time_bucket_seconds,
    )
    sampled = sorted(_frame_id(frame) for frame in sampled_frames)
    sampled_set = set(sampled)
    auto_accepted = sorted(
        _frame_id(frame)
        for frame in high_confidence
        if _frame_id(frame) not in sampled_set
    )
    selected = sorted(set(required) | sampled_set)
    return selected, sorted(required), sampled, auto_accepted


def _requires_review(
    candidates: list[dict[str, Any]], threshold: float, settings: Settings
) -> bool:
    if not candidates:
        return True
    boxes: list[YoloBox] = []
    for candidate in candidates:
        confidence = candidate.get("confidence")
        if not isinstance(confidence, (int, float)) or float(confidence) < threshold:
            return True
        box = _candidate_yolo_box(candidate)
        if touches_boundary(box):
            return True
        boxes.append(box)
    for index, first in enumerate(boxes):
        for second in boxes[index + 1 :]:
            if iou(first, second) >= settings.overlap_review_iou_threshold:
                return True
    return False


def _stratified_sample(
    frames: list[dict[str, Any]], sample_count: int, *, run_id: str, bucket_seconds: int
) -> list[dict[str, Any]]:
    if sample_count <= 0:
        return []
    groups: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for frame in frames:
        source_id = str(frame.get("source_id", ""))
        timestamp_ms = int(frame.get("timestamp_ms", 0))
        groups[(source_id, timestamp_ms // (bucket_seconds * 1000))].append(frame)
    for group_frames in groups.values():
        group_frames.sort(key=lambda frame: _sample_hash(run_id, _frame_id(frame)))
    ordered_keys = sorted(
        groups, key=lambda key: _sample_hash(run_id, f"{key[0]}:{key[1]}")
    )
    selected: list[dict[str, Any]] = []
    while len(selected) < sample_count:
        progressed = False
        for key in ordered_keys:
            if groups[key] and len(selected) < sample_count:
                selected.append(groups[key].pop(0))
                progressed = True
        if not progressed:
            break
    return selected


def _sample_hash(run_id: str, value: str) -> str:
    return hashlib.sha256(f"{run_id}:{value}".encode()).hexdigest()


def _evaluate_sample_quality(
    review_dir: Path, batch: dict[str, Any], settings: Settings
) -> dict[str, object]:
    sampled = _string_list(
        batch.get("sampled_high_confidence_frame_ids", []),
        "sampled_high_confidence_frame_ids",
    )
    if not sampled:
        return {
            "mode": "full-or-required-review",
            "passed": True,
            "true_positive": 0,
            "false_positive": 0,
            "false_negative": 0,
            "error_rate": 0.0,
        }
    run_dir = review_dir.parent.parent
    predictions = _index_by_frame_id(
        read_jsonl(run_dir / "predictions.jsonl"), "predictions.jsonl"
    )
    true_positive = false_positive = false_negative = 0
    for frame_id in sampled:
        candidates = [
            _candidate_yolo_box(candidate)
            for candidate in _candidate_rows(predictions[frame_id])
        ]
        reviewed = parse_yolo_file(review_dir / f"{frame_id}.txt")
        counts = _match_boxes(
            candidates, reviewed, settings.calibration_match_iou_threshold
        )
        true_positive += counts[0]
        false_positive += counts[1]
        false_negative += counts[2]
    denominator = true_positive + false_positive + false_negative
    error_rate = (
        0.0 if denominator == 0 else (false_positive + false_negative) / denominator
    )
    passed = false_negative == 0 and error_rate <= 0.01
    return {
        "mode": "high-confidence-sample",
        "passed": passed,
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "error_rate": error_rate,
    }


def _calibrate_camera(
    frames: list[dict[str, Any]],
    predictions: dict[str, dict[str, Any]],
    review_dir: Path,
    settings: Settings,
) -> dict[str, object]:
    sessions = {str(frame.get("session_id", "")) for frame in frames}
    confidences = sorted(
        {
            float(candidate["confidence"])
            for frame in frames
            for candidate in _candidate_rows(predictions[_frame_id(frame)])
            if isinstance(candidate.get("confidence"), (int, float))
        }
    )
    selected_threshold: float | None = None
    selected_metrics: dict[str, object] | None = None
    if (
        len(frames) >= settings.calibration_min_frames
        and len(sessions) >= settings.calibration_min_sessions
    ):
        for threshold in confidences:
            metrics = _metrics_at_threshold(
                frames,
                predictions,
                review_dir,
                threshold,
                settings.calibration_match_iou_threshold,
            )
            precision = metrics.get("precision")
            recall = metrics.get("recall")
            if not isinstance(precision, (int, float)) or not isinstance(
                recall, (int, float)
            ):
                raise AutoLabelingError("보정 성능 지표가 올바르지 않습니다.")
            if (
                precision >= settings.calibration_target_precision
                and recall >= settings.calibration_target_recall
            ):
                selected_threshold = threshold
                selected_metrics = metrics
                break
    return {
        "eligible": selected_threshold is not None,
        "frame_count": len(frames),
        "session_count": len(sessions),
        "auto_accept_threshold": selected_threshold,
        "metrics": selected_metrics,
    }


def _metrics_at_threshold(
    frames: list[dict[str, Any]],
    predictions: dict[str, dict[str, Any]],
    review_dir: Path,
    threshold: float,
    match_iou_threshold: float,
) -> dict[str, object]:
    true_positive = false_positive = false_negative = 0
    for frame in frames:
        frame_id = _frame_id(frame)
        candidates = [
            _candidate_yolo_box(candidate)
            for candidate in _candidate_rows(predictions[frame_id])
            if float(candidate.get("confidence", 0.0)) >= threshold
        ]
        reviewed = parse_yolo_file(review_dir / f"{frame_id}.txt")
        counts = _match_boxes(candidates, reviewed, match_iou_threshold)
        true_positive += counts[0]
        false_positive += counts[1]
        false_negative += counts[2]
    precision_denominator = true_positive + false_positive
    recall_denominator = true_positive + false_negative
    precision = (
        0.0 if precision_denominator == 0 else true_positive / precision_denominator
    )
    recall = 0.0 if recall_denominator == 0 else true_positive / recall_denominator
    return {
        "threshold": threshold,
        "precision": precision,
        "recall": recall,
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
    }


def _match_boxes(
    candidates: list[YoloBox], reviewed: list[YoloBox], match_iou_threshold: float
) -> tuple[int, int, int]:
    unmatched_reviewed = set(range(len(reviewed)))
    true_positive = 0
    for candidate in candidates:
        matches = [
            (iou(candidate, reviewed[index]), index)
            for index in unmatched_reviewed
            if candidate.class_id == reviewed[index].class_id
        ]
        if not matches:
            continue
        best_iou, best_index = max(matches)
        if best_iou >= match_iou_threshold:
            true_positive += 1
            unmatched_reviewed.remove(best_index)
    false_positive = len(candidates) - true_positive
    false_negative = len(reviewed) - true_positive
    return true_positive, false_positive, false_negative


def _load_calibrations(
    paths: tuple[Path, ...], *, model_sha256: str, sampling_policy_version: str
) -> dict[str, float]:
    thresholds: dict[str, float] = {}
    for path in paths:
        calibration = _require_dict(read_json(path.resolve(strict=True)), path.name)
        if calibration.get("model_sha256") != model_sha256:
            raise AutoLabelingError("보정 파일의 모델 SHA-256이 현재 실행과 다릅니다.")
        if calibration.get("sampling_policy_version") != sampling_policy_version:
            raise AutoLabelingError(
                "보정 파일의 sampling policy가 현재 실행과 다릅니다."
            )
        cameras = _require_dict(calibration.get("cameras"), "cameras")
        for camera_id, value in cameras.items():
            if not isinstance(camera_id, str) or not isinstance(value, dict):
                raise AutoLabelingError("보정 파일의 camera 항목이 올바르지 않습니다.")
            if value.get("eligible") is not True:
                continue
            threshold = value.get("auto_accept_threshold")
            if (
                not isinstance(threshold, (int, float))
                or not 0 <= float(threshold) <= 1
            ):
                raise AutoLabelingError(
                    "보정 파일의 자동 승인 임계값이 올바르지 않습니다."
                )
            if camera_id in thresholds and thresholds[camera_id] != float(threshold):
                raise AutoLabelingError(
                    "같은 카메라에 서로 다른 보정 임계값이 주어졌습니다."
                )
            thresholds[camera_id] = float(threshold)
    return thresholds


def _validate_class_files(review_dir: Path) -> None:
    for file_name in ("classes.txt", "predefined_classes.txt"):
        path = review_dir / file_name
        try:
            content = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        except OSError as exc:
            raise AutoLabelingError(f"{file_name}을 읽을 수 없습니다.") from exc
        if content != CLASS_FILE_CONTENT:
            raise AutoLabelingError(f"{file_name}은 person 한 줄이어야 합니다.")


def _validate_review_file_set(review_dir: Path, frame_ids: list[str]) -> None:
    expected = set(frame_ids)
    actual_images = {path.stem for path in review_dir.glob("*.jpg")}
    actual_labels = {
        path.stem
        for path in review_dir.glob("*.txt")
        if path.name not in {"classes.txt", "predefined_classes.txt"}
    }
    if actual_images != expected or actual_labels != expected:
        raise AutoLabelingError("검수 이미지·라벨 파일 집합이 review-batch와 다릅니다.")


def _validate_review_images(
    review_dir: Path, batch: dict[str, Any], frame_ids: list[str]
) -> None:
    image_hashes = _require_dict(batch.get("input_image_sha256"), "input_image_sha256")
    if set(image_hashes) != set(frame_ids):
        raise AutoLabelingError("검수 입력 이미지 해시 목록이 대상 프레임과 다릅니다.")
    for frame_id in frame_ids:
        image_path = review_dir / f"{frame_id}.jpg"
        if image_path.is_symlink() or image_hashes.get(frame_id) != sha256_file(
            image_path
        ):
            raise AutoLabelingError(
                f"frame_id={frame_id}: 검수 입력 이미지가 변경됐습니다."
            )


def _verify_receipt_payload(
    review_dir: Path, batch: dict[str, Any], receipt: dict[str, Any]
) -> None:
    frame_ids = _string_list(batch.get("frame_ids"), "frame_ids")
    _validate_class_files(review_dir)
    _validate_review_file_set(review_dir, frame_ids)
    _validate_review_images(review_dir, batch, frame_ids)
    if receipt.get("run_id") != batch.get("run_id") or receipt.get(
        "batch_id"
    ) != batch.get("batch_id"):
        raise AutoLabelingError("검수 완료 기록의 실행·배치 ID가 다릅니다.")
    labelimg = _require_dict(receipt.get("labelimg"), "labelimg")
    if labelimg.get("smoke_test_confirmed") is not True or not isinstance(
        labelimg.get("executable_sha256"), str
    ):
        raise AutoLabelingError("검수 완료 기록에 labelImg smoke 근거가 없습니다.")
    if receipt.get("review_batch_sha256") != sha256_file(
        review_dir / "review-batch.json"
    ):
        raise AutoLabelingError("검수 완료 뒤 review-batch.json이 변경됐습니다.")
    if receipt.get("classes_sha256") != sha256_file(review_dir / "classes.txt"):
        raise AutoLabelingError("검수 완료 뒤 classes.txt가 변경됐습니다.")
    if receipt.get("predefined_classes_sha256") != sha256_file(
        review_dir / "predefined_classes.txt"
    ):
        raise AutoLabelingError("검수 완료 뒤 predefined_classes.txt가 변경됐습니다.")
    files = receipt.get("files")
    if not isinstance(files, list):
        raise AutoLabelingError("review-completed.json의 files가 올바르지 않습니다.")
    receipt_by_frame = {
        item.get("frame_id"): item for item in files if isinstance(item, dict)
    }
    if set(receipt_by_frame) != set(frame_ids):
        raise AutoLabelingError("검수 완료 파일 목록이 review-batch와 다릅니다.")
    for frame_id in frame_ids:
        item = receipt_by_frame[frame_id]
        image_path = review_dir / f"{frame_id}.jpg"
        label_path = review_dir / f"{frame_id}.txt"
        if item.get("image_sha256") != sha256_file(image_path):
            raise AutoLabelingError(
                f"frame_id={frame_id}: 검수 완료 뒤 이미지가 변경됐습니다."
            )
        if item.get("label_sha256") != sha256_file(label_path):
            raise AutoLabelingError(
                f"frame_id={frame_id}: 검수 완료 뒤 라벨이 변경됐습니다."
            )
        parse_yolo_file(label_path)


def _candidate_rows(prediction: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = prediction.get("candidates")
    if not isinstance(candidates, list) or not all(
        isinstance(item, dict) for item in candidates
    ):
        raise AutoLabelingError("predictions.jsonl의 candidates가 올바르지 않습니다.")
    return candidates


def _candidate_yolo_box(candidate: dict[str, Any]) -> YoloBox:
    values = candidate.get("bbox_yolo")
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        raise AutoLabelingError("모델 후보의 bbox_yolo가 올바르지 않습니다.")
    try:
        box = YoloBox(0, *(float(value) for value in values))
    except (TypeError, ValueError) as exc:
        raise AutoLabelingError(
            "모델 후보의 bbox_yolo 숫자가 올바르지 않습니다."
        ) from exc
    validate_yolo_box(box, "predictions.jsonl", 1)
    return box


def _selection_fingerprint(
    selected: list[str],
    required: list[str],
    sampled: list[str],
    auto_accepted: list[str],
    *,
    force_full: bool,
    provenance: dict[str, object],
    calibrations: list[dict[str, str]],
) -> str:
    value = json.dumps(
        {
            "selected": selected,
            "required": required,
            "sampled": sampled,
            "auto_accepted": auto_accepted,
            "force_full": force_full,
            "provenance": provenance,
            "calibrations": calibrations,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(value).hexdigest()


def _calibration_records_from_batch(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise AutoLabelingError("검수 배치의 calibrations가 배열이 아닙니다.")
    records: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            raise AutoLabelingError("검수 배치의 calibration 항목이 올바르지 않습니다.")
        file_name = item.get("file_name")
        sha256 = item.get("sha256")
        if (
            not isinstance(file_name, str)
            or not file_name
            or not isinstance(sha256, str)
            or len(sha256) != 64
        ):
            raise AutoLabelingError("검수 배치의 calibration 근거가 올바르지 않습니다.")
        records.append({"file_name": file_name, "sha256": sha256})
    return records


def _index_by_frame_id(
    values: list[dict[str, Any]], file_name: str
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for value in values:
        frame_id = _frame_id(value)
        if frame_id in result:
            raise AutoLabelingError(f"{file_name}에 중복 frame_id가 있습니다.")
        result[frame_id] = value
    return result


def _frame_id(value: dict[str, Any]) -> str:
    return frame_id_from_record(value)


def _string_list(value: object, field_name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise AutoLabelingError(f"{field_name}은 문자열 배열이어야 합니다.")
    if len(value) != len(set(value)):
        raise AutoLabelingError(f"{field_name}에 중복 값이 있습니다.")
    return value


def _require_dict(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AutoLabelingError(f"{field_name}은 JSON 객체여야 합니다.")
    return value


def _model_sha256(prelabel_manifest: dict[str, Any]) -> str:
    model = _require_dict(prelabel_manifest.get("model"), "model")
    value = model.get("model_sha256")
    if not isinstance(value, str) or not value:
        raise AutoLabelingError("prelabel.json에 model SHA-256이 없습니다.")
    return value
