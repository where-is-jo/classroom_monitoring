from __future__ import annotations

import math
import shutil
import tempfile
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .core import (
    Settings,
    read_json,
    read_jsonl,
    sha256_bytes,
    sha256_file,
    utc_now_iso,
    write_json,
    write_jsonl,
)
from .errors import AutoLabelingError
from .prelabel import UltralyticsPredictor
from .preprocessing import apply_training_preprocessing
from .quality import FrameQualityThresholds, inspect_frame_quality
from .yolo import YoloBox, parse_yolo_file, write_yolo_file

ISOLATION_PHASH_HAMMING_THRESHOLD = 4
ISOLATION_PIXEL_MAE_THRESHOLD = 0.02
ISOLATION_COMPARISON_SIZE = 64


def sample_evaluation_frames(
    evaluation_manifest_path: Path,
    output_dir: Path,
    *,
    interval_seconds: float = 5.0,
    max_frames_per_video: int = 500,
    target_frame_count: int | None = None,
    jpeg_quality: int = 95,
) -> Path:
    if not math.isfinite(interval_seconds) or interval_seconds <= 0:
        raise AutoLabelingError("평가 프레임 간격은 0보다 커야 합니다.")
    if max_frames_per_video < 1:
        raise AutoLabelingError("영상당 평가 프레임 수는 1 이상이어야 합니다.")
    if target_frame_count is not None and target_frame_count < 1:
        raise AutoLabelingError("고정 평가 프레임 수는 1 이상이어야 합니다.")
    if not 1 <= jpeg_quality <= 100:
        raise AutoLabelingError("JPEG 품질은 1~100이어야 합니다.")
    manifest_path = evaluation_manifest_path.resolve(strict=True)
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("manifest_role") != "evaluation":
        raise AutoLabelingError("evaluation manifest가 아닙니다.")
    raw_sources = manifest.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise AutoLabelingError("evaluation manifest에 영상이 없습니다.")
    target = output_dir.resolve()
    if target.exists():
        raise AutoLabelingError("평가 프레임 출력 디렉터리가 이미 있습니다.")

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{target.name}-", dir=target.parent
    ) as temp:
        temporary = Path(temp)
        images_dir = temporary / "images"
        labels_dir = temporary / "labels"
        images_dir.mkdir()
        labels_dir.mkdir()
        records: list[dict[str, Any]] = []
        for raw_source in raw_sources:
            if not isinstance(raw_source, dict):
                raise AutoLabelingError("evaluation source가 객체가 아닙니다.")
            records.extend(
                _sample_source(
                    raw_source,
                    images_dir,
                    labels_dir,
                    interval_seconds=interval_seconds,
                    max_frames=max_frames_per_video,
                    jpeg_quality=jpeg_quality,
                )
            )
        if not records:
            raise AutoLabelingError("추출된 평가 프레임이 없습니다.")
        quality_failures: list[dict[str, object]] = []
        clean_records: list[dict[str, Any]] = []
        if target_frame_count is None:
            clean_records = records
        else:
            seen_image_hashes: set[str] = set()
            for record in records:
                image_path = temporary / str(record["image_path"])
                quality = inspect_frame_quality(image_path, FrameQualityThresholds())
                digest = str(record["image_sha256"])
                reasons = list(quality.get("reasons", []))
                if digest in seen_image_hashes:
                    reasons.append("exact-duplicate-frame")
                if reasons:
                    quality_failures.append(
                        {
                            "frame_id": record["frame_id"],
                            "source_id": record["source_id"],
                            "timestamp_ms": record["timestamp_ms"],
                            "reasons": reasons,
                        }
                    )
                    continue
                seen_image_hashes.add(digest)
                clean_records.append(record)
        records, allocations = _select_evaluation_records(
            clean_records,
            target_frame_count,
        )
        selected_ids = {str(record["frame_id"]) for record in records}
        for image_path in images_dir.glob("*.jpg"):
            if image_path.stem not in selected_ids:
                image_path.unlink()
        for label_path in labels_dir.glob("*.txt"):
            if label_path.stem not in selected_ids:
                label_path.unlink()
        write_jsonl(temporary / "evaluation_frames.jsonl", records)
        (temporary / "classes.txt").write_text("person\n", encoding="utf-8")
        (temporary / "data.yaml").write_text(
            "path: .\ntrain: images\nval: images\nnc: 1\nnames:\n  0: person\n",
            encoding="utf-8",
            newline="\n",
        )
        write_json(
            temporary / "evaluation_set.json",
            {
                "schema_version": 1,
                "evaluation_id": manifest.get("evaluation_id"),
                "source_manifest_sha256": sha256_file(manifest_path),
                "interval_seconds": interval_seconds,
                "max_frames_per_video": max_frames_per_video,
                "target_frame_count": target_frame_count,
                "jpeg_quality": jpeg_quality,
                "frame_count": len(records),
                "quality_failed_frame_count": len(quality_failures),
                "quality_failures": quality_failures,
                "source_allocations": allocations,
                "class_names": ["person"],
                "status": "awaiting-manual-review",
            },
        )
        temporary.replace(target)
    return target


def prelabel_evaluation_set(
    evaluation_dir: Path,
    model_path: Path,
    settings: Settings,
    *,
    device: str,
    expected_model_sha256: str | None = None,
    image_size: int | None = None,
    input_preprocessing: dict[str, object] | None = None,
) -> Path:
    """고정 Test 이미지에 후보 라벨을 만들고 모델·입력 계약을 기록한다."""

    root = evaluation_dir.resolve(strict=True)
    metadata_path = root / "evaluation_set.json"
    metadata = read_json(metadata_path)
    if (
        not isinstance(metadata, dict)
        or metadata.get("status") != "awaiting-manual-review"
    ):
        raise AutoLabelingError("수동 검수 대기 중인 평가 세트가 아닙니다.")
    model = model_path.resolve(strict=True)
    observed_sha256 = sha256_file(model)
    if expected_model_sha256 is not None and observed_sha256 != expected_model_sha256:
        raise AutoLabelingError("평가 자동 라벨링 모델 SHA-256이 다릅니다.")
    receipt_path = root / "evaluation_prelabel.json"
    records = read_jsonl(root / "evaluation_frames.jsonl")
    if receipt_path.exists():
        receipt = read_json(receipt_path)
        if not isinstance(receipt, dict):
            raise AutoLabelingError("평가 자동 라벨링 영수증이 올바르지 않습니다.")
        expected = {
            "model_sha256": observed_sha256,
            "image_size": image_size,
            "input_preprocessing": input_preprocessing,
            "frames_manifest_sha256": sha256_file(root / "evaluation_frames.jsonl"),
        }
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise AutoLabelingError(
                "기존 평가 자동 라벨링 계약이 현재 설정과 다릅니다."
            )
        return receipt_path

    predictor = UltralyticsPredictor(
        model,
        confidence_threshold=settings.candidate_confidence_threshold,
        device=device,
        image_size=image_size,
        input_preprocessing=input_preprocessing,
    )
    files: list[dict[str, object]] = []
    for record in records:
        frame_id = str(record.get("frame_id", ""))
        image_path = root / "images" / f"{frame_id}.jpg"
        label_path = root / "labels" / f"{frame_id}.txt"
        if label_path.read_text(encoding="utf-8").strip():
            raise AutoLabelingError(
                "평가 후보 라벨 생성 전 라벨 파일이 비어 있어야 합니다."
            )
        candidates = predictor.predict(image_path)
        write_yolo_file(
            label_path,
            [
                YoloBox(candidate.class_id, *candidate.bbox_yolo)
                for candidate in candidates
            ],
        )
        files.append(
            {
                "frame_id": frame_id,
                "image_sha256": sha256_file(image_path),
                "candidate_count": len(candidates),
                "candidate_label_sha256": sha256_file(label_path),
            }
        )
    write_json(
        receipt_path,
        {
            "schema_version": 1,
            "model_file_name": model.name,
            "model_sha256": observed_sha256,
            "device": device,
            "image_size": image_size,
            "input_preprocessing": input_preprocessing,
            "candidate_confidence_threshold": settings.candidate_confidence_threshold,
            "frames_manifest_sha256": sha256_file(root / "evaluation_frames.jsonl"),
            "files": files,
            "created_at": utc_now_iso(),
        },
    )
    return receipt_path


def _select_evaluation_records(
    records: list[dict[str, Any]],
    target_frame_count: int | None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if not records:
        raise AutoLabelingError("품질 검사를 통과한 평가 프레임이 없습니다.")
    by_source: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        source_id = str(record.get("source_id", ""))
        if not source_id:
            raise AutoLabelingError("평가 프레임의 source_id가 비어 있습니다.")
        by_source.setdefault(source_id, []).append(record)
    for values in by_source.values():
        values.sort(key=lambda item: int(item.get("timestamp_ms", 0)))

    target = len(records) if target_frame_count is None else target_frame_count
    if target > len(records):
        raise AutoLabelingError(
            f"정상 평가 프레임 {len(records)}장은 목표 {target}장보다 적습니다."
        )
    source_ids = sorted(by_source)
    if target < len(source_ids):
        raise AutoLabelingError(
            "고정 평가 프레임 수가 평가 원본 영상 수보다 적습니다. "
            "모든 영상에서 한 장 이상 선택할 수 있어야 합니다."
        )
    allocations = {source_id: 1 for source_id in source_ids}
    remaining = target - len(source_ids)
    while remaining:
        progressed = False
        for source_id in source_ids:
            if allocations[source_id] >= len(by_source[source_id]):
                continue
            allocations[source_id] += 1
            remaining -= 1
            progressed = True
            if remaining == 0:
                break
        if not progressed:
            raise AutoLabelingError("평가 프레임 목표 수를 배분할 수 없습니다.")

    selected: list[dict[str, Any]] = []
    for source_id in source_ids:
        values = by_source[source_id]
        count = allocations[source_id]
        if count == len(values):
            selected.extend(values)
            continue
        indices = (
            [round(index * (len(values) - 1) / (count - 1)) for index in range(count)]
            if count > 1
            else [len(values) // 2]
        )
        selected.extend(values[index] for index in indices)
    selected.sort(
        key=lambda item: (
            str(item.get("session_id", "")),
            str(item.get("source_id", "")),
            int(item.get("timestamp_ms", 0)),
        )
    )
    if (
        len(selected) != target
        or len({item["frame_id"] for item in selected}) != target
    ):
        raise AutoLabelingError("고정 평가 프레임 선택 결과가 목표 수와 다릅니다.")
    return selected, allocations


def freeze_evaluation_set(
    evaluation_dir: Path,
    *,
    reviewer_id: str,
    training_dataset_dir: Path | None = None,
) -> Path:
    if not reviewer_id.strip():
        raise AutoLabelingError("reviewer_id가 필요합니다.")
    root = evaluation_dir.resolve(strict=True)
    metadata = read_json(root / "evaluation_set.json")
    if not isinstance(metadata, dict) or metadata.get("schema_version") != 1:
        raise AutoLabelingError("evaluation_set.json이 올바르지 않습니다.")
    records = read_jsonl(root / "evaluation_frames.jsonl")
    files: list[dict[str, str]] = []
    for record in records:
        frame_id = str(record.get("frame_id", ""))
        image_path = root / "images" / f"{frame_id}.jpg"
        label_path = root / "labels" / f"{frame_id}.txt"
        if not image_path.is_file() or not label_path.is_file():
            raise AutoLabelingError(
                f"frame_id={frame_id}: 평가 이미지 또는 라벨이 없습니다."
            )
        parse_yolo_file(label_path)
        files.append(
            {
                "frame_id": frame_id,
                "image_sha256": sha256_file(image_path),
                "label_sha256": sha256_file(label_path),
            }
        )
    isolation = (
        verify_evaluation_isolation(root, training_dataset_dir)
        if training_dataset_dir is not None
        else {"passed": False, "reason": "training_dataset_dir-not-provided"}
    )
    if training_dataset_dir is not None and isolation.get("passed") is not True:
        raise AutoLabelingError("평가 프레임이 학습 데이터와 중복됩니다.")
    receipt_path = root / "evaluation_frozen.json"
    if receipt_path.exists():
        raise AutoLabelingError("평가 세트는 이미 동결됐습니다.")
    write_json(
        receipt_path,
        {
            "schema_version": 1,
            "frozen_at": utc_now_iso(),
            "reviewer_id": reviewer_id.strip(),
            "evaluation_set_sha256": sha256_file(root / "evaluation_set.json"),
            "frames_manifest_sha256": sha256_file(root / "evaluation_frames.jsonl"),
            "frame_count": len(files),
            "files": files,
            "isolation": isolation,
        },
    )
    metadata["status"] = "frozen"
    metadata["frozen_receipt_sha256"] = sha256_file(receipt_path)
    write_json(root / "evaluation_set.json", metadata)
    return receipt_path


def verify_evaluation_isolation(
    evaluation_dir: Path, training_dataset_dir: Path
) -> dict[str, Any]:
    evaluation_root = evaluation_dir.resolve(strict=True)
    training_root = training_dataset_dir.resolve(strict=True)
    training_images = sorted((training_root / "images").glob("*/*.jpg"))
    evaluation_images = sorted((evaluation_root / "images").glob("*.jpg"))
    return _verify_image_paths(
        evaluation_images,
        training_images,
        candidate_name="evaluation",
        reference_name="training",
    )


def verify_image_set_isolation(
    candidate_image_dir: Path, reference_image_dir: Path
) -> dict[str, Any]:
    """두 이미지 디렉터리의 exact/확인된 near-duplicate 누출을 검사한다."""

    candidate_root = candidate_image_dir.resolve(strict=True)
    reference_root = reference_image_dir.resolve(strict=True)
    candidate_images = sorted(candidate_root.rglob("*.jpg"))
    reference_images = sorted(reference_root.rglob("*.jpg"))
    return _verify_image_paths(
        candidate_images,
        reference_images,
        candidate_name="candidate",
        reference_name="reference",
    )


def _verify_image_paths(
    candidate_images: list[Path],
    reference_images: list[Path],
    *,
    candidate_name: str,
    reference_name: str,
) -> dict[str, Any]:
    if not candidate_images or not reference_images:
        raise AutoLabelingError("누출 검사를 위한 비교 이미지가 없습니다.")
    reference_exact: dict[str, str] = {}
    reference_features: list[tuple[int, np.ndarray, str]] = []
    for path in reference_images:
        reference_exact[sha256_file(path)] = path.name
        phash, comparison = _image_features(path)
        reference_features.append((phash, comparison, path.name))
    collisions: list[dict[str, object]] = []
    perceptual_candidate_count = 0
    rejected_perceptual_candidate_count = 0
    for path in candidate_images:
        digest = sha256_file(path)
        exact = reference_exact.get(digest)
        if exact:
            collisions.append(
                {
                    candidate_name: path.name,
                    reference_name: exact,
                    "type": "exact-sha256",
                    "distance": 0,
                }
            )
            continue
        value, comparison = _image_features(path)
        confirmed_matches: list[tuple[int, float, str]] = []
        for reference_phash, reference_comparison, reference_file in reference_features:
            distance = _hamming(value, reference_phash)
            if distance > ISOLATION_PHASH_HAMMING_THRESHOLD:
                continue
            perceptual_candidate_count += 1
            pixel_mae = float(np.mean(np.abs(comparison - reference_comparison)))
            if pixel_mae > ISOLATION_PIXEL_MAE_THRESHOLD:
                rejected_perceptual_candidate_count += 1
                continue
            confirmed_matches.append((distance, pixel_mae, reference_file))
        if confirmed_matches:
            distance, pixel_mae, reference_file = min(
                confirmed_matches, key=lambda item: item
            )
            collisions.append(
                {
                    candidate_name: path.name,
                    reference_name: reference_file,
                    "type": "perceptual-near-duplicate",
                    "distance": distance,
                    "pixel_mae": pixel_mae,
                }
            )
    return {
        "passed": not collisions,
        "policy": ("exact-sha256-or-dct-phash-hamming-4-and-pixel-mae-0.02-v2"),
        "phash_hamming_threshold": ISOLATION_PHASH_HAMMING_THRESHOLD,
        "pixel_mae_threshold": ISOLATION_PIXEL_MAE_THRESHOLD,
        "comparison_size": ISOLATION_COMPARISON_SIZE,
        f"{reference_name}_image_count": len(reference_images),
        f"{candidate_name}_image_count": len(candidate_images),
        "perceptual_candidate_count": perceptual_candidate_count,
        "rejected_perceptual_candidate_count": (rejected_perceptual_candidate_count),
        "collisions": collisions,
    }


def verify_frozen_evaluation_set(evaluation_dir: Path) -> dict[str, Any]:
    root = evaluation_dir.resolve(strict=True)
    receipt_path = root / "evaluation_frozen.json"
    receipt = read_json(receipt_path)
    if not isinstance(receipt, dict):
        raise AutoLabelingError("평가 동결 영수증이 올바르지 않습니다.")
    files = receipt.get("files")
    if not isinstance(files, list) or not files:
        raise AutoLabelingError("평가 동결 영수증에 파일이 없습니다.")
    for item in files:
        if not isinstance(item, dict):
            raise AutoLabelingError("평가 동결 파일 항목이 올바르지 않습니다.")
        frame_id = str(item.get("frame_id", ""))
        image_path = root / "images" / f"{frame_id}.jpg"
        label_path = root / "labels" / f"{frame_id}.txt"
        if sha256_file(image_path) != item.get("image_sha256"):
            raise AutoLabelingError("동결 후 평가 이미지가 변경됐습니다.")
        if sha256_file(label_path) != item.get("label_sha256"):
            raise AutoLabelingError("동결 후 평가 라벨이 변경됐습니다.")
        parse_yolo_file(label_path)
    return {
        "status": "valid",
        "frame_count": len(files),
        "evaluation_frozen_sha256": sha256_file(receipt_path),
        "evaluation_set_sha256": receipt.get("evaluation_set_sha256"),
        "frames_manifest_sha256": receipt.get("frames_manifest_sha256"),
    }


def materialize_preprocessed_evaluation_set(
    source_evaluation_dir: Path,
    output_dir: Path,
    preprocessing_contract: dict[str, object],
) -> Path:
    """동결 Test를 변경하지 않고 실제 추론 전처리와 같은 파생 Test를 만든다."""

    if preprocessing_contract.get("label_derived") is not False:
        raise AutoLabelingError("평가 전처리는 정답 라벨과 독립적이어야 합니다.")
    if preprocessing_contract.get("training_compatible") is not True:
        raise AutoLabelingError("평가 전처리 계약이 학습 호환이 아닙니다.")
    source = source_evaluation_dir.resolve(strict=True)
    source_verification = verify_frozen_evaluation_set(source)
    target = output_dir.resolve()
    if target.exists():
        raise AutoLabelingError("전처리 평가 세트 출력 디렉터리가 이미 있습니다.")

    records = read_jsonl(source / "evaluation_frames.jsonl")
    metadata = read_json(source / "evaluation_set.json")
    source_receipt = read_json(source / "evaluation_frozen.json")
    if not isinstance(metadata, dict) or not isinstance(source_receipt, dict):
        raise AutoLabelingError("원본 동결 평가 메타데이터가 올바르지 않습니다.")

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{target.name}-", dir=target.parent
    ) as temp:
        temporary = Path(temp)
        images_dir = temporary / "images"
        labels_dir = temporary / "labels"
        images_dir.mkdir()
        labels_dir.mkdir()
        derived_records: list[dict[str, Any]] = []
        frozen_files: list[dict[str, str]] = []
        for raw_record in records:
            record = dict(raw_record)
            frame_id = str(record.get("frame_id", ""))
            source_image = source / "images" / f"{frame_id}.jpg"
            source_label = source / "labels" / f"{frame_id}.txt"
            image = cv2.imread(str(source_image))
            if image is None:
                raise AutoLabelingError(
                    f"frame_id={frame_id}: 원본 평가 이미지를 읽을 수 없습니다."
                )
            processed = apply_training_preprocessing(image, preprocessing_contract)
            target_image = images_dir / f"{frame_id}.jpg"
            target_label = labels_dir / f"{frame_id}.txt"
            if not cv2.imwrite(
                str(target_image), processed, [cv2.IMWRITE_JPEG_QUALITY, 95]
            ):
                raise AutoLabelingError(
                    f"frame_id={frame_id}: 전처리 평가 이미지를 저장할 수 없습니다."
                )
            shutil.copy2(source_label, target_label)
            record["image_sha256"] = sha256_file(target_image)
            derived_records.append(record)
            frozen_files.append(
                {
                    "frame_id": frame_id,
                    "image_sha256": sha256_file(target_image),
                    "label_sha256": sha256_file(target_label),
                }
            )

        write_jsonl(temporary / "evaluation_frames.jsonl", derived_records)
        (temporary / "classes.txt").write_text("person\n", encoding="utf-8")
        (temporary / "data.yaml").write_text(
            "path: .\ntrain: images\nval: images\nnc: 1\nnames:\n  0: person\n",
            encoding="utf-8",
            newline="\n",
        )
        derived_metadata = dict(metadata)
        derived_metadata["evaluation_id"] = (
            f"{metadata.get('evaluation_id', source.name)}-preprocessed"
        )
        derived_metadata["status"] = "frozen"
        derived_metadata["source_evaluation_frozen_sha256"] = source_verification[
            "evaluation_frozen_sha256"
        ]
        derived_metadata["preprocessing_contract"] = preprocessing_contract
        derived_metadata.pop("frozen_receipt_sha256", None)
        write_json(temporary / "evaluation_set.json", derived_metadata)
        write_json(
            temporary / "evaluation_frozen.json",
            {
                "schema_version": 1,
                "frozen_at": utc_now_iso(),
                "reviewer_id": "derived-from-manually-reviewed-frozen-set",
                "evaluation_set_sha256": sha256_file(temporary / "evaluation_set.json"),
                "frames_manifest_sha256": sha256_file(
                    temporary / "evaluation_frames.jsonl"
                ),
                "frame_count": len(frozen_files),
                "files": frozen_files,
                "isolation": {
                    "passed": True,
                    "reason": "label-independent-derivative-of-isolated-frozen-set",
                    "source_isolation": source_receipt.get("isolation"),
                    "source_evaluation_frozen_sha256": source_verification[
                        "evaluation_frozen_sha256"
                    ],
                },
                "preprocessing_contract": preprocessing_contract,
            },
        )
        derived_metadata["frozen_receipt_sha256"] = sha256_file(
            temporary / "evaluation_frozen.json"
        )
        write_json(temporary / "evaluation_set.json", derived_metadata)
        verify_frozen_evaluation_set(temporary)
        temporary.replace(target)
    return target


def _sample_source(
    source: dict[str, Any],
    images_dir: Path,
    labels_dir: Path,
    *,
    interval_seconds: float,
    max_frames: int,
    jpeg_quality: int,
) -> list[dict[str, Any]]:
    source_id = str(source.get("source_id", ""))
    source_sha256 = str(source.get("source_sha256", ""))
    file_path = Path(str(source.get("file_path", ""))).resolve(strict=True)
    if file_path.suffix.lower() != ".mp4":
        raise AutoLabelingError(f"source_id={source_id}: MP4만 평가할 수 있습니다.")
    if sha256_file(file_path) != source_sha256:
        raise AutoLabelingError(
            f"source_id={source_id}: 평가 원본 해시가 변경됐습니다."
        )
    capture = cv2.VideoCapture(str(file_path))
    try:
        if not capture.isOpened():
            raise AutoLabelingError(
                f"source_id={source_id}: 평가 영상을 열 수 없습니다."
            )
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        if fps <= 0:
            raise AutoLabelingError(f"source_id={source_id}: FPS를 확인할 수 없습니다.")
        reported_frame_count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        interval_frames = max(1, round(fps * interval_seconds))
        frame_index = 0
        records: list[dict[str, Any]] = []
        while len(records) < max_frames:
            read_ok, frame = capture.read()
            if not read_ok:
                break
            if frame_index % interval_frames == 0:
                timestamp_ms = round(frame_index * 1000 / fps)
                frame_id = sha256_bytes(
                    f"{source_sha256}:{timestamp_ms}:evaluation-v1".encode()
                )[:24]
                image_path = images_dir / f"{frame_id}.jpg"
                if not cv2.imwrite(
                    str(image_path), frame, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality]
                ):
                    raise AutoLabelingError("평가 프레임 JPEG를 저장할 수 없습니다.")
                (labels_dir / f"{frame_id}.txt").write_text("", encoding="utf-8")
                records.append(
                    {
                        "frame_id": frame_id,
                        "source_id": source_id,
                        "source_sha256": source_sha256,
                        "session_id": source.get("session_id"),
                        "camera_id": source.get("camera_id"),
                        "evaluation_scope": source.get("evaluation_scope"),
                        "timestamp_ms": timestamp_ms,
                        "image_path": f"images/{frame_id}.jpg",
                        "label_path": f"labels/{frame_id}.txt",
                        "image_sha256": sha256_file(image_path),
                    }
                )
            frame_index += 1
        if len(records) < max_frames and reported_frame_count > 0:
            tolerance = max(1, round(reported_frame_count * 0.001))
            if frame_index + tolerance < round(reported_frame_count):
                raise AutoLabelingError(
                    f"source_id={source_id}: 평가 영상 디코딩이 끝까지 도달하지 "
                    f"못했습니다({frame_index}/{round(reported_frame_count)} frames)."
                )
        return records
    finally:
        capture.release()


def _image_features(path: Path) -> tuple[int, np.ndarray]:
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise AutoLabelingError(f"이미지를 읽을 수 없습니다: {path.name}")
    phash_image = cv2.resize(image, (32, 32), interpolation=cv2.INTER_AREA)
    transformed = cv2.dct(phash_image.astype(np.float32))[:8, :8]
    values = transformed.flatten()
    median = float(np.median(values[1:]))
    bits = values > median
    result = 0
    for bit in bits:
        result = (result << 1) | int(bit)
    comparison = cv2.resize(
        image,
        (ISOLATION_COMPARISON_SIZE, ISOLATION_COMPARISON_SIZE),
        interpolation=cv2.INTER_AREA,
    ).astype(np.float32)
    comparison /= 255.0
    return result, comparison


def _hamming(first: int, second: int) -> int:
    return (first ^ second).bit_count()
