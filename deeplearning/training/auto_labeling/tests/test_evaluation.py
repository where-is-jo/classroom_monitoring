from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from auto_labeling import evaluation
from auto_labeling.core import (
    CandidateBox,
    load_settings,
    read_json,
    read_jsonl,
    sha256_file,
    write_json,
    write_jsonl,
)
from auto_labeling.errors import AutoLabelingError
from auto_labeling.evaluation import (
    freeze_evaluation_set,
    materialize_preprocessed_evaluation_set,
    prelabel_evaluation_set,
    sample_evaluation_frames,
    verify_evaluation_isolation,
    verify_frozen_evaluation_set,
    verify_image_set_isolation,
)
from auto_labeling.preprocessing import original_frame_contract
from auto_labeling.yolo import parse_yolo_file


def _write_video(path: Path) -> None:
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),  # type: ignore[attr-defined]
        10.0,
        (64, 48),
    )
    assert writer.isOpened()
    for index in range(20):
        frame = np.zeros((48, 64, 3), dtype=np.uint8)
        cv2.rectangle(frame, (index, 8), (index + 10, 35), (255, 255, 255), -1)
        writer.write(frame)
    writer.release()


def _write_quality_video(path: Path, *, seed: int) -> None:
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),  # type: ignore[attr-defined]
        10.0,
        (128, 96),
    )
    assert writer.isOpened()
    random = np.random.default_rng(seed)
    base = random.integers(48, 208, size=(96, 128, 3), dtype=np.uint8)
    for index in range(50):
        frame = base.copy()
        cv2.rectangle(frame, (index, 18), (index + 20, 82), (240, 220, 200), -1)
        writer.write(frame)
    writer.release()


def test_evaluation_frames_are_separate_and_hash_frozen(tmp_path: Path) -> None:
    video = tmp_path / "evaluation.mp4"
    _write_video(video)
    manifest = tmp_path / "evaluation_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "manifest_role": "evaluation",
                "evaluation_id": "evaluation-001",
                "sources": [
                    {
                        "source_id": "source-001",
                        "source_sha256": sha256_file(video),
                        "file_path": str(video),
                        "session_id": "session-evaluation",
                        "camera_id": "camera-01",
                        "evaluation_scope": "benchmark",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    output = sample_evaluation_frames(
        manifest,
        tmp_path / "evaluation-set",
        interval_seconds=0.5,
        max_frames_per_video=3,
    )
    records = read_jsonl(output / "evaluation_frames.jsonl")
    assert len(records) == 3
    assert (
        read_json(output / "evaluation_set.json")["status"] == "awaiting-manual-review"
    )
    data_yaml = (output / "data.yaml").read_text(encoding="utf-8")
    assert "train: images\n" in data_yaml
    assert "val: images\n" in data_yaml
    receipt = freeze_evaluation_set(output, reviewer_id="reviewer-001")
    assert receipt.is_file()
    verification = verify_frozen_evaluation_set(output)
    assert verification["frame_count"] == 3
    assert verification["evaluation_frozen_sha256"] == sha256_file(receipt)
    assert verification["frames_manifest_sha256"] == sha256_file(
        output / "evaluation_frames.jsonl"
    )

    first_label = output / records[0]["label_path"]
    first_label.write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
    with pytest.raises(AutoLabelingError, match="동결 후 평가 라벨"):
        verify_frozen_evaluation_set(output)


def test_evaluation_target_count_is_exact_and_spread_across_sources(
    tmp_path: Path,
) -> None:
    videos = [tmp_path / "first.mp4", tmp_path / "second.mp4"]
    for index, video in enumerate(videos):
        _write_quality_video(video, seed=index)
    manifest = tmp_path / "evaluation_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "manifest_role": "evaluation",
                "evaluation_id": "fixed-test-001",
                "sources": [
                    {
                        "source_id": f"source-{index}",
                        "source_sha256": sha256_file(video),
                        "file_path": str(video),
                        "session_id": f"session-{index}",
                        "camera_id": "camera-01",
                        "evaluation_scope": "benchmark",
                    }
                    for index, video in enumerate(videos)
                ],
            }
        ),
        encoding="utf-8",
    )

    output = sample_evaluation_frames(
        manifest,
        tmp_path / "fixed-test",
        interval_seconds=0.1,
        max_frames_per_video=20,
        target_frame_count=6,
    )

    records = read_jsonl(output / "evaluation_frames.jsonl")
    metadata = read_json(output / "evaluation_set.json")
    assert len(records) == 6
    assert metadata["target_frame_count"] == 6
    assert metadata["source_allocations"] == {"source-0": 3, "source-1": 3}
    assert {record["source_id"] for record in records} == {"source-0", "source-1"}


def test_fixed_evaluation_prelabel_records_model_and_candidate_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "evaluation"
    (root / "images").mkdir(parents=True)
    (root / "labels").mkdir()
    image_path = root / "images" / "frame-001.jpg"
    assert cv2.imwrite(str(image_path), np.full((96, 128, 3), 96, dtype=np.uint8))
    (root / "labels" / "frame-001.txt").write_text("", encoding="utf-8")
    write_jsonl(
        root / "evaluation_frames.jsonl",
        [
            {
                "frame_id": "frame-001",
                "source_id": "source-001",
                "image_sha256": sha256_file(image_path),
            }
        ],
    )
    write_json(
        root / "evaluation_set.json",
        {"schema_version": 1, "status": "awaiting-manual-review", "frame_count": 1},
    )
    model_path = tmp_path / "yolo26n.pt"
    model_path.write_bytes(b"weights")

    class FixedPredictor:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def predict(self, _image_path: Path) -> list[CandidateBox]:
            return [
                CandidateBox(
                    class_id=0,
                    class_name="person",
                    confidence=0.9,
                    bbox_xyxy_pixels=(32.0, 24.0, 96.0, 72.0),
                    bbox_yolo=(0.5, 0.5, 0.5, 0.5),
                )
            ]

    monkeypatch.setattr(evaluation, "UltralyticsPredictor", FixedPredictor)

    receipt_path = prelabel_evaluation_set(
        root,
        model_path,
        load_settings(),
        device="cpu",
        expected_model_sha256=sha256_file(model_path),
        image_size=1280,
        input_preprocessing=original_frame_contract(),
    )

    receipt = read_json(receipt_path)
    assert receipt["model_file_name"] == "yolo26n.pt"
    assert receipt["image_size"] == 1280
    assert len(parse_yolo_file(root / "labels" / "frame-001.txt")) == 1


def test_evaluation_isolation_rejects_exact_duplicate(tmp_path: Path) -> None:
    training, evaluation = _isolation_directories(tmp_path)
    image = np.full((80, 120, 3), 96, dtype=np.uint8)
    assert cv2.imwrite(str(training / "train.jpg"), image)
    assert cv2.imwrite(str(evaluation / "evaluation.jpg"), image)

    report = verify_evaluation_isolation(
        evaluation.parent,
        training.parent.parent,
    )

    assert report["passed"] is False
    assert report["collisions"] == [
        {
            "evaluation": "evaluation.jpg",
            "training": "train.jpg",
            "type": "exact-sha256",
            "distance": 0,
        }
    ]


def test_evaluation_isolation_rejects_confirmed_near_duplicate(
    tmp_path: Path,
) -> None:
    training, evaluation = _isolation_directories(tmp_path)
    assert cv2.imwrite(
        str(training / "train.jpg"), np.full((80, 120, 3), 96, dtype=np.uint8)
    )
    assert cv2.imwrite(
        str(evaluation / "evaluation.jpg"),
        np.full((80, 120, 3), 97, dtype=np.uint8),
    )

    report = verify_evaluation_isolation(
        evaluation.parent,
        training.parent.parent,
    )

    assert report["passed"] is False
    collision = report["collisions"][0]
    assert collision["type"] == "perceptual-near-duplicate"
    assert collision["distance"] <= 4
    assert collision["pixel_mae"] <= 0.02


def test_evaluation_isolation_rejects_phash_only_false_positive(
    tmp_path: Path,
) -> None:
    training, evaluation = _isolation_directories(tmp_path)
    assert cv2.imwrite(
        str(training / "train.jpg"), np.full((80, 120, 3), 64, dtype=np.uint8)
    )
    assert cv2.imwrite(
        str(evaluation / "evaluation.jpg"),
        np.full((80, 120, 3), 192, dtype=np.uint8),
    )

    report = verify_evaluation_isolation(
        evaluation.parent,
        training.parent.parent,
    )

    assert report["passed"] is True
    assert report["perceptual_candidate_count"] >= 1
    assert report["rejected_perceptual_candidate_count"] >= 1
    assert report["collisions"] == []


def test_image_set_isolation_accepts_recursive_directories(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate" / "frames"
    reference = tmp_path / "reference" / "images" / "train"
    candidate.mkdir(parents=True)
    reference.mkdir(parents=True)
    assert cv2.imwrite(
        str(candidate / "candidate.jpg"),
        np.full((80, 120, 3), 32, dtype=np.uint8),
    )
    assert cv2.imwrite(
        str(reference / "reference.jpg"),
        np.full((80, 120, 3), 224, dtype=np.uint8),
    )

    report = verify_image_set_isolation(candidate.parent, reference.parent.parent)

    assert report["passed"] is True
    assert report["candidate_image_count"] == 1
    assert report["reference_image_count"] == 1


def test_materialize_preprocessed_evaluation_preserves_labels_and_source_freeze(
    tmp_path: Path,
) -> None:
    video = tmp_path / "evaluation.mp4"
    _write_video(video)
    manifest = tmp_path / "evaluation_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "manifest_role": "evaluation",
                "evaluation_id": "evaluation-001",
                "sources": [
                    {
                        "source_id": "source-001",
                        "source_sha256": sha256_file(video),
                        "file_path": str(video),
                        "session_id": "session-evaluation",
                        "camera_id": "camera-01",
                        "evaluation_scope": "benchmark",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    source = sample_evaluation_frames(
        manifest,
        tmp_path / "evaluation-source",
        interval_seconds=0.5,
        max_frames_per_video=2,
    )
    freeze_evaluation_set(source, reviewer_id="reviewer-001")
    source_verification = verify_frozen_evaluation_set(source)
    contract = {
        "schema_version": 1,
        "method": "uniform-full-frame-pixelation-v1",
        "label_derived": False,
        "training_compatible": True,
        "inference_preprocessing_required": True,
        "pixelation_block_size": 8,
    }

    derived = materialize_preprocessed_evaluation_set(
        source,
        tmp_path / "evaluation-derived",
        contract,
    )

    verification = verify_frozen_evaluation_set(derived)
    metadata = read_json(derived / "evaluation_set.json")
    assert verification["frame_count"] == 2
    assert metadata["preprocessing_contract"] == contract
    assert (
        metadata["source_evaluation_frozen_sha256"]
        == source_verification["evaluation_frozen_sha256"]
    )
    first = read_jsonl(source / "evaluation_frames.jsonl")[0]["frame_id"]
    assert sha256_file(source / "labels" / f"{first}.txt") == sha256_file(
        derived / "labels" / f"{first}.txt"
    )
    assert sha256_file(source / "images" / f"{first}.jpg") != sha256_file(
        derived / "images" / f"{first}.jpg"
    )


def _isolation_directories(tmp_path: Path) -> tuple[Path, Path]:
    training = tmp_path / "dataset" / "images" / "train"
    evaluation = tmp_path / "evaluation" / "images"
    training.mkdir(parents=True)
    evaluation.mkdir(parents=True)
    return training, evaluation
