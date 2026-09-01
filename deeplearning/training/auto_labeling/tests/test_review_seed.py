from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from auto_labeling.core import read_json, sha256_file
from auto_labeling.review_seed import (
    merge_manual_audit_subset,
    migrate_manual_audit_labels,
    prepare_manual_audit_subset,
    seed_review_from_verified_reviews,
)


def test_seed_review_restores_verified_labels_and_flags_oversized_box(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = tmp_path / "pilot"
    target = run / "review" / "review-pilot"
    candidate_dir = run / "candidate-labels"
    source = tmp_path / "source-review"
    target.mkdir(parents=True)
    candidate_dir.mkdir()
    source.mkdir()
    frame_ids = ["frame-001", "frame-002"]
    frames = []
    for index, frame_id in enumerate(frame_ids):
        image = np.full((80, 120, 3), 50 + index * 20, dtype=np.uint8)
        assert cv2.imwrite(str(target / f"{frame_id}.jpg"), image)
        assert cv2.imwrite(str(source / f"{frame_id}.jpg"), image)
        candidate = "0 0.5 0.4 0.2 0.2\n"
        reviewed = "0 0.5 0.55 0.4 0.5\n" if index == 0 else candidate
        (candidate_dir / f"{frame_id}.txt").write_text(candidate, encoding="utf-8")
        (target / f"{frame_id}.txt").write_text(candidate, encoding="utf-8")
        (source / f"{frame_id}.txt").write_text(reviewed, encoding="utf-8")
        frames.append(
            {
                "frame_id": frame_id,
                "session_id": "session-001",
            }
        )
    (run / "frames.jsonl").write_text(
        "".join(json.dumps(frame) + "\n" for frame in frames),
        encoding="utf-8",
    )
    (target / "review-batch.json").write_text(
        json.dumps(
            {
                "run_id": "pilot",
                "batch_id": "review-pilot",
                "frame_ids": frame_ids,
            }
        ),
        encoding="utf-8",
    )
    (target / "classes.txt").write_text("person\n", encoding="utf-8")
    (target / "predefined_classes.txt").write_text("person\n", encoding="utf-8")
    monkeypatch.setattr(
        "auto_labeling.review_seed.verify_review_batch_provenance",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "auto_labeling.review_seed.verify_review_receipt",
        lambda _path: {
            "run_id": "source",
            "batch_id": "review-source",
            "reviewer_id": "reviewer-001",
            "files": [{"frame_id": frame_id} for frame_id in frame_ids],
        },
    )
    (source / "review-completed.json").write_text("{}", encoding="utf-8")

    receipt_path = seed_review_from_verified_reviews(
        target,
        [source],
        spot_check_fraction=0.5,
    )

    receipt = read_json(receipt_path)
    assert receipt["frame_count"] == 2
    assert receipt["changed_label_count"] == 1
    assert receipt["manual_audit_frame_count"] >= 1
    first_reasons = next(
        item["reasons"]
        for item in receipt["manual_audit_frames"]
        if item["frame_id"] == "frame-001"
    )
    assert "reviewed-box-area-much-larger-than-yolo11" in first_reasons
    audit_rows = (target / "manual-audit.csv").read_text(encoding="utf-8").splitlines()
    audit_ids = [row.split(",", 1)[0] for row in audit_rows[1:]]
    assert set(audit_ids) == {
        item["frame_id"] for item in receipt["manual_audit_frames"]
    }
    assert sha256_file(target / "frame-001.txt") == sha256_file(
        source / "frame-001.txt"
    )
    assert seed_review_from_verified_reviews(target, [source]) == receipt_path

    audit_dir = prepare_manual_audit_subset(target)
    assert len(list(audit_dir.glob("*.jpg"))) == receipt["manual_audit_frame_count"]
    (audit_dir / "classes.txt").write_text("person\r\n\r\n", encoding="utf-8")
    audited_frame_id = audit_ids[0]
    changed_label = "0 0.5 0.5 0.1 0.1\n"
    (audit_dir / f"{audited_frame_id}.txt").write_text(
        changed_label,
        encoding="utf-8",
    )

    merge_receipt = read_json(merge_manual_audit_subset(target))
    assert merge_receipt["frame_count"] == receipt["manual_audit_frame_count"]
    assert (target / f"{audited_frame_id}.txt").read_text(
        encoding="utf-8"
    ) == changed_label

    migrated_target = run / "review" / "review-migrated"
    migrated_target.mkdir()
    for frame_id in frame_ids:
        (migrated_target / f"{frame_id}.jpg").write_bytes(
            (target / f"{frame_id}.jpg").read_bytes()
        )
        (migrated_target / f"{frame_id}.txt").write_text(
            "0 0.5 0.5 0.2 0.2\n",
            encoding="utf-8",
        )
    (migrated_target / "review-batch.json").write_text(
        json.dumps(
            {
                "run_id": "pilot-migrated",
                "batch_id": "review-migrated",
                "frame_ids": frame_ids,
            }
        ),
        encoding="utf-8",
    )
    migration = read_json(migrate_manual_audit_labels(migrated_target, target))
    assert migration["migrated_frame_count"] == receipt["manual_audit_frame_count"]
    assert (migrated_target / f"{audited_frame_id}.txt").read_text(
        encoding="utf-8"
    ) == changed_label
