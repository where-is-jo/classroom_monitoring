from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from auto_labeling import privacy
from auto_labeling.errors import AutoLabelingError


def _dataset(tmp_path: Path) -> Path:
    root = tmp_path / "dataset" / "person-v0001"
    image_path = root / "images" / "train" / "frame-001.jpg"
    label_path = root / "labels" / "train" / "frame-001.txt"
    image_path.parent.mkdir(parents=True)
    label_path.parent.mkdir(parents=True)
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    for y in range(100):
        image[y, :, :] = y * 2
    assert cv2.imwrite(str(image_path), image)
    label_path.write_text("0 0.5 0.5 0.8 0.8\n", encoding="utf-8")
    (root / "data.yaml").write_text(
        "path: .\ntrain: images/train\nval: images/val\nnc: 1\nnames:\n  0: person\n",
        encoding="utf-8",
    )
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "dataset_version": "person-v0001",
                "items": [
                    {
                        "frame_id": "frame-001",
                        "split": "train",
                        "image_path": "images/train/frame-001.jpg",
                        "label_path": "labels/train/frame-001.txt",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return root


def test_colab_export_requires_an_approval_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = _dataset(tmp_path)
    monkeypatch.setattr(
        privacy,
        "validate_dataset",
        lambda path: {"dataset_version": "person-v0001", "frame_count": 1},
    )

    with pytest.raises(AutoLabelingError, match="승인된 학생 집단 정책"):
        privacy.export_deidentified_dataset(
            dataset,
            tmp_path / "export",
            operator_id="operator-001",
        )


def test_colab_export_has_receipt_and_no_source_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = _dataset(tmp_path)
    monkeypatch.setattr(
        privacy,
        "validate_dataset",
        lambda path: {"dataset_version": "person-v0001", "frame_count": 1},
    )

    export = privacy.export_deidentified_dataset(
        dataset,
        tmp_path / "export",
        operator_id="operator-001",
        manual_privacy_review_confirmed=True,
    )
    report = privacy.validate_privacy_export(export)
    manifest_text = (export / "manifest.json").read_text(encoding="utf-8")

    assert report["status"] == "valid"
    assert report["image_count"] == 1
    assert report["training_compatible"] is True
    assert report["preprocessing_contract"] == {
        "schema_version": 1,
        "method": "uniform-full-frame-pixelation-v1",
        "label_derived": False,
        "training_compatible": True,
        "inference_preprocessing_required": True,
        "pixelation_block_size": 8,
    }
    assert str(dataset) not in manifest_text
    assert (export / "privacy_receipt.json").is_file()


def test_colab_export_accepts_approved_student_cohort_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = _dataset(tmp_path)
    monkeypatch.setattr(
        privacy,
        "validate_dataset",
        lambda path: {"dataset_version": "person-v0001", "frame_count": 1},
    )

    export = privacy.export_deidentified_dataset(
        dataset,
        tmp_path / "export",
        operator_id="person-detection-pipeline-auto",
        approved_cohort_policy="ai-student-cohort-person-detection-v1",
    )
    report = privacy.validate_privacy_export(export)
    receipt = json.loads((export / "privacy_receipt.json").read_text(encoding="utf-8"))

    assert report["approval_mode"] == "approved-student-cohort-policy"
    assert receipt["manual_privacy_review_confirmed"] is False
    assert receipt["approval_reference"] == "ai-student-cohort-person-detection-v1"


def test_extend_colab_export_adds_reviewed_val_and_applies_exclusions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = _dataset(tmp_path)
    monkeypatch.setattr(
        privacy,
        "validate_dataset",
        lambda path: {"dataset_version": "person-v0001", "frame_count": 1},
    )
    base_export = privacy.export_deidentified_dataset(
        dataset,
        tmp_path / "base-export",
        operator_id="operator-001",
        approved_cohort_policy="approved-cohort-v1",
    )

    review = tmp_path / "review-val"
    review.mkdir()
    frame_ids = ["frame-val-001", "frame-val-excluded"]
    for index, frame_id in enumerate(frame_ids):
        image = np.full((100, 100, 3), 50 + index * 50, dtype=np.uint8)
        assert cv2.imwrite(str(review / f"{frame_id}.jpg"), image)
        (review / f"{frame_id}.txt").write_text("0 0.5 0.5 0.8 0.8\n", encoding="utf-8")
    (review / "review-batch.json").write_text(
        json.dumps(
            {
                "run_id": "run-val",
                "batch_id": "review-val",
                "frame_ids": frame_ids,
            }
        ),
        encoding="utf-8",
    )
    (review / "review-completed.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        privacy,
        "verify_review_receipt",
        lambda path: {"reviewer_id": "reviewer-001"},
    )

    excluded_frame = frame_ids[1]
    exclusion = tmp_path / "exclusions.json"
    exclusion.write_text(
        json.dumps(
            {
                "run_id": "run-val",
                "batch_id": "review-val",
                "review_batch_sha256": privacy.sha256_file(
                    review / "review-batch.json"
                ),
                "excluded_frame_count": 1,
                "excluded_frames": [
                    {
                        "frame_id": excluded_frame,
                        "image_sha256": privacy.sha256_file(
                            review / f"{excluded_frame}.jpg"
                        ),
                        "label_sha256": privacy.sha256_file(
                            review / f"{excluded_frame}.txt"
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    output = privacy.extend_deidentified_dataset_with_reviewed_validation(
        base_export,
        [review],
        tmp_path / "combined-export",
        operator_id="operator-001",
        approved_cohort_policy="approved-cohort-v1",
        dataset_version="person-v0002",
        exclusion_receipt_paths=[exclusion],
    )
    report = privacy.validate_privacy_export(output)

    assert report["image_count"] == 2
    assert report["split_counts"] == {"train": 1, "val": 1}
    assert (output / "images" / "val" / "frame-val-001.jpg").is_file()
    assert not (output / "images" / "val" / f"{excluded_frame}.jpg").exists()


def test_extend_colab_export_adds_reviewed_background_negatives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = _dataset(tmp_path)
    monkeypatch.setattr(
        privacy,
        "validate_dataset",
        lambda path: {"dataset_version": "person-v0001", "frame_count": 1},
    )
    base_export = privacy.export_deidentified_dataset(
        dataset,
        tmp_path / "base-export",
        operator_id="operator-001",
        approved_cohort_policy="approved-cohort-v1",
    )
    base_report = privacy.validate_privacy_export(base_export)

    review = tmp_path / "review-negatives"
    images = review / "images"
    images.mkdir(parents=True)
    negative_image = images / "negative-001.jpg"
    assert cv2.imwrite(str(negative_image), np.full((64, 64, 3), 120, dtype=np.uint8))
    (review / "negative_review.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "reviewer_id": "reviewer-001",
                "manual_visual_review_confirmed": True,
                "preprocessing_contract": base_report["preprocessing_contract"],
                "items": [
                    {
                        "frame_id": "negative-001",
                        "image_path": "images/negative-001.jpg",
                        "image_sha256": privacy.sha256_file(negative_image),
                        "source_video_name": "source.mp4",
                        "source_time_seconds": 10.0,
                        "crop_xyxy": [0, 0, 64, 64],
                        "no_person_confirmed": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    output = privacy.extend_deidentified_dataset_with_reviewed_negatives(
        base_export,
        review,
        tmp_path / "combined-export",
        operator_id="operator-001",
        approved_cohort_policy="approved-cohort-v1",
        dataset_version="person-v0002-negative1",
    )
    report = privacy.validate_privacy_export(output)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))

    assert report["split_counts"] == {"train": 2, "val": 0}
    assert manifest["negative_count"] == 1
    assert manifest["artifact_type"].endswith("background-negatives")
    assert (output / "labels" / "train" / "negative-001.txt").read_text(
        encoding="utf-8"
    ) == ""


def test_legacy_bbox_pixelation_is_not_training_compatible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = _dataset(tmp_path)
    monkeypatch.setattr(
        privacy,
        "validate_dataset",
        lambda path: {"dataset_version": "person-v0001", "frame_count": 1},
    )

    export = privacy.export_deidentified_dataset(
        dataset,
        tmp_path / "legacy-export",
        operator_id="operator-001",
        approved_cohort_policy="approved-cohort-v1",
        preprocessing_method="person-bbox-top-pixelation-v1",
    )

    report = privacy.validate_privacy_export(export)
    assert report["training_compatible"] is False
    assert report["preprocessing_contract"]["label_derived"] is True
