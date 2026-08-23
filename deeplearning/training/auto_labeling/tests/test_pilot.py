from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from auto_labeling.core import read_json, read_jsonl, sha256_file
from auto_labeling.pilot import (
    PilotSessionPlan,
    _promote_temporary_directory,
    prepare_clean_pilot_run,
    replace_pilot_frame,
)


def _source_run(
    root: Path,
    *,
    run_id: str,
    session_id: str,
    source_counts: dict[str, int],
    corrupt_frame: tuple[str, int] | None = None,
) -> Path:
    run = root / run_id
    frames_dir = run / "frames"
    frames_dir.mkdir(parents=True)
    frames = []
    sources = []
    for source_id, count in source_counts.items():
        sources.append(
            {
                "source_id": source_id,
                "session_id": session_id,
                "camera_id": "camera-01",
                "approval_reference": "approval-001",
                "consent_scope": "person-detection-training",
                "retention_expires_at": "2027-12-28T23:59:59+09:00",
                "subject_category": "student",
                "usage": "dataset",
                "requested_split": "train",
                "frame_count": count,
            }
        )
        for index in range(count):
            frame_id = f"{run_id}-{source_id}-{index:03d}"
            y, x = np.indices((120, 160))
            image = np.stack(
                (
                    (x + index * 3) % 200 + 20,
                    (y + index * 5) % 200 + 20,
                    (x + y + index * 7) % 200 + 20,
                ),
                axis=2,
            ).astype(np.uint8)
            if corrupt_frame == (source_id, index):
                image[:] = (0, 128, 0)
            image_path = frames_dir / f"{frame_id}.jpg"
            assert cv2.imwrite(str(image_path), image)
            frames.append(
                {
                    "frame_id": frame_id,
                    "source_id": source_id,
                    "source_sha256": "a" * 64,
                    "timestamp_ms": index * 2000,
                    "camera_id": "camera-01",
                    "session_id": session_id,
                    "captured_at": "2026-08-20T09:00:00+09:00",
                    "approval_reference": "approval-001",
                    "consent_scope": "person-detection-training",
                    "retention_expires_at": "2027-12-28T23:59:59+09:00",
                    "subject_category": "student",
                    "usage": "dataset",
                    "requested_split": "train",
                    "image_path": f"frames/{frame_id}.jpg",
                    "image_sha256": sha256_file(image_path),
                }
            )
    (run / "frames.jsonl").write_text(
        "".join(json.dumps(frame) + "\n" for frame in frames),
        encoding="utf-8",
    )
    (run / "run.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": run_id,
                "frame_count": len(frames),
                "sampling_policy_version": "source-v1",
                "approved_student_data": True,
                "sources": sources,
            }
        ),
        encoding="utf-8",
    )
    return run


def test_prepare_clean_pilot_run_filters_corruption_and_preserves_session_split(
    tmp_path: Path,
) -> None:
    first = _source_run(
        tmp_path,
        run_id="source-run-1",
        session_id="session-train",
        source_counts={"source-a": 6, "source-b": 6},
        corrupt_frame=("source-a", 2),
    )
    second = _source_run(
        tmp_path,
        run_id="source-run-2",
        session_id="session-val",
        source_counts={"source-c": 5},
    )

    output = prepare_clean_pilot_run(
        [first, second],
        tmp_path / "pilots",
        run_id="clean-pilot-v001",
        session_plan={
            "session-train": PilotSessionPlan("train", 6),
            "session-val": PilotSessionPlan("val", 3),
        },
    )

    frames = read_jsonl(output / "frames.jsonl")
    report = read_json(output / "quality-report.json")
    assert len(frames) == 9
    assert sum(frame["requested_split"] == "train" for frame in frames) == 6
    assert sum(frame["requested_split"] == "val" for frame in frames) == 3
    assert report["quality_failed_frame_count"] == 1
    assert report["quality_failures"][0]["reasons"] == [
        "dominant-green-corruption",
        "flat-color-frame",
        "dominant-green-horizontal-band",
    ]
    assert len(list((output / "frames").glob("*.jpg"))) == 9


def test_prepare_clean_pilot_run_excludes_operator_confirmed_bad_frame(
    tmp_path: Path,
) -> None:
    source = _source_run(
        tmp_path,
        run_id="source-run-manual-exclusion",
        session_id="session-train",
        source_counts={"source-a": 5},
    )
    excluded_id = "source-run-manual-exclusion-source-a-002"

    output = prepare_clean_pilot_run(
        [source],
        tmp_path / "pilots",
        run_id="clean-pilot-manual-exclusion",
        session_plan={"session-train": PilotSessionPlan("train", 4)},
        excluded_frame_ids=[excluded_id],
    )

    frame_ids = {frame["frame_id"] for frame in read_jsonl(output / "frames.jsonl")}
    report = read_json(output / "quality-report.json")
    assert excluded_id not in frame_ids
    assert report["manually_excluded_frame_count"] == 1
    assert report["manual_exclusions"] == [
        {
            "frame_id": excluded_id,
            "session_id": "session-train",
            "source_id": "source-a",
            "timestamp_ms": 4000,
            "reason": "operator-confirmed-recording-or-transmission-error",
        }
    ]


def test_prepare_clean_pilot_run_falls_back_when_directory_replace_is_denied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temporary = tmp_path / "temporary-pilot"
    target = tmp_path / "clean-pilot-windows-copy"
    (temporary / "frames").mkdir(parents=True)
    (temporary / "frames" / "frame-001.jpg").write_bytes(b"image")
    (temporary / "run.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        Path,
        "replace",
        lambda _source, _target: (_ for _ in ()).throw(PermissionError()),
    )
    _promote_temporary_directory(temporary, target)

    assert target.is_dir()
    assert (target / "frames" / "frame-001.jpg").read_bytes() == b"image"


def test_replace_pilot_frame_changes_exactly_one_frame(tmp_path: Path) -> None:
    source = _source_run(
        tmp_path,
        run_id="source-run-replacement",
        session_id="session-train",
        source_counts={"source-a": 6},
    )
    parent = prepare_clean_pilot_run(
        [source],
        tmp_path / "pilots",
        run_id="clean-pilot-parent",
        session_plan={"session-train": PilotSessionPlan("train", 3)},
    )
    parent_ids = {frame["frame_id"] for frame in read_jsonl(parent / "frames.jsonl")}
    source_ids = {frame["frame_id"] for frame in read_jsonl(source / "frames.jsonl")}
    excluded_id = sorted(parent_ids)[0]
    replacement_id = sorted(source_ids - parent_ids)[0]

    output = replace_pilot_frame(
        parent,
        [source],
        tmp_path / "pilots",
        run_id="clean-pilot-replaced",
        excluded_frame_id=excluded_id,
        replacement_frame_id=replacement_id,
    )

    output_ids = {frame["frame_id"] for frame in read_jsonl(output / "frames.jsonl")}
    report = read_json(output / "quality-report.json")
    assert parent_ids - output_ids == {excluded_id}
    assert output_ids - parent_ids == {replacement_id}
    assert report["status"] == "passed"
    assert report["targeted_replacements"][0]["excluded_frame_id"] == excluded_id
