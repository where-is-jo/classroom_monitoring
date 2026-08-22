from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml

from auto_labeling.core import (
    CandidateBox,
    Settings,
    load_settings,
    read_json,
    read_jsonl,
    sha256_file,
    write_json,
    write_jsonl,
)
from auto_labeling.errors import AutoLabelingError
from auto_labeling.prelabel import ModelInfo, generate_candidate_labels
from auto_labeling.prepare import prepare_run
from auto_labeling.publish import publish_dataset, validate_dataset
from auto_labeling.review import complete_review, create_calibration, prepare_review


class FixedPredictor:
    def predict(self, image_path: Path) -> list[CandidateBox]:
        image = cv2.imread(str(image_path))
        assert image is not None
        height, width = image.shape[:2]
        return [
            CandidateBox(
                class_id=0,
                class_name="person",
                confidence=0.95,
                bbox_xyxy_pixels=(10.0, 10.0, width - 10.0, height - 10.0),
                bbox_yolo=(0.5, 0.5, (width - 20) / width, (height - 20) / height),
            )
        ]


@pytest.fixture
def prepared_run(tmp_path: Path) -> tuple[Path, Settings, Path]:
    video_path = tmp_path / "synthetic.mp4"
    _write_synthetic_video(video_path)
    manifest_path = tmp_path / "input.json"
    manifest_path.write_text(
        json.dumps(
            {
                "run_id": "synthetic-run",
                "sources": [
                    {
                        "source_id": "source-001",
                        "file_path": str(video_path),
                        "approval_reference": "synthetic-approval",
                        "consent_scope": "person-detection-training",
                        "retention_expires_at": "2099-01-01T00:00:00+00:00",
                        "camera_id": "camera-001",
                        "session_id": "session-001",
                        "captured_at": "2026-08-18T09:00:00+09:00",
                        "subject_category": "synthetic",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    settings = load_settings()
    runs_root = tmp_path / "runs"
    run_dir = prepare_run(manifest_path, settings, output_root=runs_root)
    generate_candidate_labels(
        run_dir,
        FixedPredictor(),
        ModelInfo(
            model_path="synthetic://fixed-predictor",
            model_file_name="synthetic.pt",
            model_sha256="f" * 64,
            model_runtime="test-double",
            model_runtime_version="1",
            device="cpu",
        ),
        settings,
    )
    labelimg_executable = tmp_path / "labelImg.exe"
    labelimg_executable.write_bytes(b"synthetic-labelimg-binary")
    return run_dir, settings, labelimg_executable


def test_full_workflow_is_idempotent(
    prepared_run: tuple[Path, Settings, Path], tmp_path: Path
) -> None:
    run_dir, settings, labelimg_executable = prepared_run
    review_dir = prepare_review(run_dir, settings)
    receipt_path = complete_review(
        review_dir,
        "reviewer-001",
        settings,
        labelimg_executable=labelimg_executable,
        labelimg_smoke_confirmed=True,
    )
    datasets_root = tmp_path / "datasets"

    same_run_dir = prepare_run(
        tmp_path / "input.json", settings, output_root=tmp_path / "runs"
    )
    dataset_dir = publish_dataset(run_dir, dataset_root=datasets_root)
    same_dataset_dir = publish_dataset(run_dir, dataset_root=datasets_root)
    report = validate_dataset(dataset_dir)

    assert receipt_path.is_file()
    assert run_dir == same_run_dir
    assert dataset_dir == same_dataset_dir
    assert dataset_dir.name == "person-v0001"
    assert report["status"] == "pilot"
    assert report["frame_count"] == 3
    assert report["split_counts"] == {"train": 3, "val": 0}
    deduplication_report = report["deduplication"]
    assert isinstance(deduplication_report, dict)
    assert deduplication_report["removed_frame_count"] == 0
    assert read_json(dataset_dir / "manifest.json")["schema_version"] == 3
    assert (dataset_dir / "deduplication.jsonl").is_file()


def test_publish_removes_exact_duplicate_frames(
    prepared_run: tuple[Path, Settings, Path], tmp_path: Path
) -> None:
    run_dir, settings, labelimg_executable = prepared_run
    frame_ids = _make_run_frames_exact_duplicates(run_dir)
    review_dir = prepare_review(run_dir, settings)
    complete_review(
        review_dir,
        "reviewer-001",
        settings,
        labelimg_executable=labelimg_executable,
        labelimg_smoke_confirmed=True,
    )

    dataset_dir = publish_dataset(
        run_dir, dataset_root=tmp_path / "datasets", settings=settings
    )
    manifest = read_json(dataset_dir / "manifest.json")
    report_rows = read_jsonl(dataset_dir / "deduplication.jsonl")

    assert len(manifest["items"]) == 1
    assert manifest["items"][0]["frame_id"] == min(frame_ids)
    assert manifest["deduplication"]["input_frame_count"] == 3
    assert manifest["deduplication"]["removed_frame_count"] == 2
    assert len(report_rows) == 1
    assert len(list((run_dir / "frames").glob("*.jpg"))) == 3


def test_publish_rejects_exact_duplicate_with_conflicting_review_labels(
    prepared_run: tuple[Path, Settings, Path], tmp_path: Path
) -> None:
    run_dir, settings, labelimg_executable = prepared_run
    _make_run_frames_exact_duplicates(run_dir)
    review_dir = prepare_review(run_dir, settings)
    label_paths = sorted(
        path
        for path in review_dir.glob("*.txt")
        if path.name not in {"classes.txt", "predefined_classes.txt"}
    )
    label_paths[0].write_text(
        "0 0.20000000 0.20000000 0.10000000 0.10000000\n",
        encoding="utf-8",
    )
    complete_review(
        review_dir,
        "reviewer-001",
        settings,
        labelimg_executable=labelimg_executable,
        labelimg_smoke_confirmed=True,
    )

    with pytest.raises(AutoLabelingError, match="서로 다른 검수 라벨"):
        publish_dataset(run_dir, dataset_root=tmp_path / "datasets", settings=settings)


def test_validate_keeps_schema_v1_compatibility(
    prepared_run: tuple[Path, Settings, Path], tmp_path: Path
) -> None:
    run_dir, settings, labelimg_executable = prepared_run
    review_dir = prepare_review(run_dir, settings)
    complete_review(
        review_dir,
        "reviewer-001",
        settings,
        labelimg_executable=labelimg_executable,
        labelimg_smoke_confirmed=True,
    )
    dataset_dir = publish_dataset(
        run_dir, dataset_root=tmp_path / "datasets", settings=settings
    )
    legacy_dir = tmp_path / "legacy" / "person-v0001"
    shutil.copytree(dataset_dir, legacy_dir)
    manifest = read_json(legacy_dir / "manifest.json")
    manifest["schema_version"] = 1
    manifest.pop("deduplication")
    for item in manifest["items"]:
        item.pop("duplicate_group_id")
    write_json(legacy_dir / "manifest.json", manifest)
    (legacy_dir / "deduplication.jsonl").unlink()
    (legacy_dir / "images" / "test").mkdir()
    (legacy_dir / "labels" / "test").mkdir()
    data_config = yaml.safe_load((legacy_dir / "data.yaml").read_text(encoding="utf-8"))
    data_config["test"] = "images/test"
    (legacy_dir / "data.yaml").write_text(
        yaml.safe_dump(data_config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    report = validate_dataset(legacy_dir)

    assert report["frame_count"] == 3
    deduplication_report = report["deduplication"]
    assert isinstance(deduplication_report, dict)
    assert deduplication_report["removed_frame_count"] == 0


def test_validate_rejects_changed_deduplication_report(
    prepared_run: tuple[Path, Settings, Path], tmp_path: Path
) -> None:
    run_dir, settings, labelimg_executable = prepared_run
    review_dir = prepare_review(run_dir, settings)
    complete_review(
        review_dir,
        "reviewer-001",
        settings,
        labelimg_executable=labelimg_executable,
        labelimg_smoke_confirmed=True,
    )
    dataset_dir = publish_dataset(
        run_dir, dataset_root=tmp_path / "datasets", settings=settings
    )
    (dataset_dir / "deduplication.jsonl").write_text("{}\n", encoding="utf-8")

    with pytest.raises(AutoLabelingError, match="보고서 해시"):
        validate_dataset(dataset_dir)


def test_review_receipt_detects_label_changes(
    prepared_run: tuple[Path, Settings, Path], tmp_path: Path
) -> None:
    run_dir, settings, labelimg_executable = prepared_run
    review_dir = prepare_review(run_dir, settings)
    complete_review(
        review_dir,
        "reviewer-001",
        settings,
        labelimg_executable=labelimg_executable,
        labelimg_smoke_confirmed=True,
    )
    first_label = next(
        path
        for path in review_dir.glob("*.txt")
        if path.name not in {"classes.txt", "predefined_classes.txt"}
    )
    first_label.write_text("", encoding="utf-8")

    with pytest.raises(AutoLabelingError, match="완료 뒤 라벨"):
        publish_dataset(run_dir, dataset_root=tmp_path / "datasets")


def test_publish_rejects_incomplete_review(
    prepared_run: tuple[Path, Settings, Path], tmp_path: Path
) -> None:
    run_dir, settings, _ = prepared_run
    prepare_review(run_dir, settings)

    with pytest.raises(AutoLabelingError, match="발행 가능한 검수 완료 배치"):
        publish_dataset(run_dir, dataset_root=tmp_path / "datasets")


def test_publish_rejects_frame_changed_after_review(
    prepared_run: tuple[Path, Settings, Path], tmp_path: Path
) -> None:
    run_dir, settings, labelimg_executable = prepared_run
    review_dir = prepare_review(run_dir, settings)
    complete_review(
        review_dir,
        "reviewer-001",
        settings,
        labelimg_executable=labelimg_executable,
        labelimg_smoke_confirmed=True,
    )
    next((run_dir / "frames").glob("*.jpg")).write_bytes(b"changed")

    with pytest.raises(AutoLabelingError, match="이미지가 변경"):
        publish_dataset(run_dir, dataset_root=tmp_path / "datasets")


def test_review_rejects_changed_class_list(
    prepared_run: tuple[Path, Settings, Path],
) -> None:
    run_dir, settings, labelimg_executable = prepared_run
    review_dir = prepare_review(run_dir, settings)
    (review_dir / "classes.txt").write_text("student\n", encoding="utf-8")

    with pytest.raises(AutoLabelingError, match="person 한 줄"):
        complete_review(
            review_dir,
            "reviewer-001",
            settings,
            labelimg_executable=labelimg_executable,
            labelimg_smoke_confirmed=True,
        )


def test_review_requires_labelimg_smoke_confirmation(
    prepared_run: tuple[Path, Settings, Path],
) -> None:
    run_dir, settings, labelimg_executable = prepared_run
    review_dir = prepare_review(run_dir, settings)

    with pytest.raises(AutoLabelingError, match="smoke test"):
        complete_review(
            review_dir,
            "reviewer-001",
            settings,
            labelimg_executable=labelimg_executable,
            labelimg_smoke_confirmed=False,
        )


def test_calibration_enables_sampled_review(
    prepared_run: tuple[Path, Settings, Path],
) -> None:
    run_dir, base_settings, labelimg_executable = prepared_run
    settings = replace(
        base_settings,
        calibration_min_frames=1,
        calibration_min_sessions=1,
        review_sample_min_frames=1,
    )
    full_review_dir = prepare_review(
        run_dir, settings, batch_id="review-full", force_full=True
    )
    complete_review(
        full_review_dir,
        "reviewer-001",
        settings,
        labelimg_executable=labelimg_executable,
        labelimg_smoke_confirmed=True,
    )
    calibration_path = create_calibration(run_dir, full_review_dir, settings)
    calibration = read_json(calibration_path)

    sampled_review_dir = prepare_review(
        run_dir,
        settings,
        batch_id="review-sampled",
        calibration_paths=(calibration_path,),
    )
    sampled_batch = read_json(sampled_review_dir / "review-batch.json")
    complete_review(
        sampled_review_dir,
        "reviewer-002",
        settings,
        labelimg_executable=labelimg_executable,
        labelimg_smoke_confirmed=True,
    )

    assert calibration["cameras"]["camera-001"]["eligible"] is True
    assert len(sampled_batch["frame_ids"]) == 1
    assert len(sampled_batch["auto_accepted_frame_ids"]) == 2


def test_failed_sample_creates_full_fallback_review(
    prepared_run: tuple[Path, Settings, Path],
) -> None:
    run_dir, settings, labelimg_executable = _calibrated_test_run(prepared_run)
    calibration_path = run_dir / "calibration.json"
    sampled_review_dir = prepare_review(
        run_dir,
        settings,
        batch_id="review-sampled-fail",
        calibration_paths=(calibration_path,),
    )
    sampled_batch = read_json(sampled_review_dir / "review-batch.json")
    sampled_frame_id = sampled_batch["sampled_high_confidence_frame_ids"][0]
    with (sampled_review_dir / f"{sampled_frame_id}.txt").open(
        "a", encoding="utf-8"
    ) as label_file:
        label_file.write("0 0.10000000 0.10000000 0.10000000 0.10000000\n")

    receipt_path = complete_review(
        sampled_review_dir,
        "reviewer-002",
        settings,
        labelimg_executable=labelimg_executable,
        labelimg_smoke_confirmed=True,
    )
    receipt = read_json(receipt_path)
    fallback_batch_id = receipt["quality_gate"]["fallback_batch_id"]
    fallback_batch = read_json(
        run_dir / "review" / fallback_batch_id / "review-batch.json"
    )

    assert receipt["quality_gate"]["passed"] is False
    assert len(fallback_batch["frame_ids"]) == 3
    assert fallback_batch["auto_accepted_frame_ids"] == []


def test_publish_rejects_changed_auto_accepted_label(
    prepared_run: tuple[Path, Settings, Path], tmp_path: Path
) -> None:
    run_dir, settings, labelimg_executable = _calibrated_test_run(prepared_run)
    sampled_review_dir = prepare_review(
        run_dir,
        settings,
        batch_id="review-sampled-publish",
        calibration_paths=(run_dir / "calibration.json",),
    )
    sampled_batch = read_json(sampled_review_dir / "review-batch.json")
    complete_review(
        sampled_review_dir,
        "reviewer-002",
        settings,
        labelimg_executable=labelimg_executable,
        labelimg_smoke_confirmed=True,
    )
    auto_accepted_frame_id = sampled_batch["auto_accepted_frame_ids"][0]
    (run_dir / "candidate-labels" / f"{auto_accepted_frame_id}.txt").write_text(
        "", encoding="utf-8"
    )

    with pytest.raises(AutoLabelingError, match="후보 생성 뒤 라벨"):
        publish_dataset(run_dir, dataset_root=tmp_path / "datasets")


def test_corrupted_mp4_is_rejected(tmp_path: Path) -> None:
    video_path = tmp_path / "broken.mp4"
    video_path.write_bytes(b"broken")
    manifest_path = tmp_path / "input.json"
    manifest_path.write_text(
        json.dumps(
            {
                "run_id": "broken-run",
                "sources": [
                    {
                        "source_id": "source-001",
                        "file_path": str(video_path),
                        "approval_reference": "synthetic-approval",
                        "consent_scope": "person-detection-training",
                        "retention_expires_at": "2099-01-01T00:00:00+00:00",
                        "camera_id": "camera-001",
                        "session_id": "session-001",
                        "captured_at": "2026-08-18T09:00:00+09:00",
                        "subject_category": "synthetic",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(AutoLabelingError, match="영상을 열 수 없습니다"):
        prepare_run(manifest_path, load_settings(), output_root=tmp_path / "runs")


def _write_synthetic_video(path: Path) -> None:
    width, height, fps = 160, 120, 2.0
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (width, height),
    )
    assert writer.isOpened()
    try:
        for index in range(10):
            frame = np.zeros((height, width, 3), dtype=np.uint8)
            frame[:, :] = ((index * 19) % 255, (index * 31) % 255, (index * 47) % 255)
            cv2.rectangle(
                frame, (10 + index, 10), (80 + index, 110), (255, 255, 255), 2
            )
            cv2.putText(
                frame,
                str(index),
                (100, 60),
                cv2.FONT_HERSHEY_SIMPLEX,
                1,
                (0, 0, 0),
                2,
            )
            writer.write(frame)
    finally:
        writer.release()


def _calibrated_test_run(
    prepared_run: tuple[Path, Settings, Path],
) -> tuple[Path, Settings, Path]:
    run_dir, base_settings, labelimg_executable = prepared_run
    settings = replace(
        base_settings,
        calibration_min_frames=1,
        calibration_min_sessions=1,
        review_sample_min_frames=1,
    )
    full_review_dir = prepare_review(
        run_dir, settings, batch_id="review-calibration", force_full=True
    )
    complete_review(
        full_review_dir,
        "reviewer-001",
        settings,
        labelimg_executable=labelimg_executable,
        labelimg_smoke_confirmed=True,
    )
    create_calibration(run_dir, full_review_dir, settings)
    return run_dir, settings, labelimg_executable


def _make_run_frames_exact_duplicates(run_dir: Path) -> list[str]:
    frames = read_jsonl(run_dir / "frames.jsonl")
    first_image_path = run_dir / str(frames[0]["image_path"])
    for frame in frames[1:]:
        image_path = run_dir / str(frame["image_path"])
        shutil.copy2(first_image_path, image_path)
        frame["image_sha256"] = sha256_file(image_path)
    write_jsonl(run_dir / "frames.jsonl", frames)
    return [str(frame["frame_id"]) for frame in frames]
