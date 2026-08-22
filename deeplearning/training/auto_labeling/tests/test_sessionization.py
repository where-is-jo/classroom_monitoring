from __future__ import annotations

import csv
from pathlib import Path

import pytest

from auto_labeling.core import read_json, read_jsonl
from auto_labeling.errors import AutoLabelingError
from auto_labeling.partition import (
    partition_sessions,
    partition_validation_extension,
)
from auto_labeling.sessionization import ProbeResult, scan_video_folder


def _probe(path: Path) -> ProbeResult:
    durations = {
        "20260819_090000.mp4": 300.0,
        "20260819_090500.mp4": 300.0,
        "20260819_091000.mp4": 300.0,
        "20260819_091600.mp4": 300.0,
        "20260819_092201.mp4": 120.0,
    }
    return ProbeResult(durations.get(path.name, 300.0), None, "test-probe")


def _video(path: Path, marker: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(marker.encode())
    return path


def test_five_minute_clips_are_grouped_by_end_to_start_gap(tmp_path: Path) -> None:
    root = tmp_path / "videos"
    for index, name in enumerate(
        [
            "20260819_090000.mp4",
            "20260819_090500.mp4",
            "20260819_091000.mp4",
            "20260819_091600.mp4",
            "20260819_092201.mp4",
        ]
    ):
        _video(root / "camera-01" / name, str(index))

    output = scan_video_folder(root, tmp_path / "scan", duration_probe=_probe)
    manifest = read_json(output / "session_manifest.json")

    assert manifest["session_count"] == 2
    assert manifest["sessions"][0]["clip_count"] == 4
    assert manifest["sessions"][0]["gap_seconds"] == [0.0, 0.0, 60.0]
    assert manifest["sessions"][1]["clip_count"] == 1


def test_different_cameras_never_share_session_and_ids_are_stable(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    for root in (first_root, second_root):
        _video(root / "camera-01" / "20260819_090000.mp4", "camera-one")
        _video(root / "camera-02" / "20260819_090000.mp4", "camera-two")

    first = scan_video_folder(
        first_root, tmp_path / "scan-first", duration_probe=_probe
    )
    second = scan_video_folder(
        second_root, tmp_path / "scan-second", duration_probe=_probe
    )
    first_sessions = read_json(first / "session_manifest.json")["sessions"]
    second_sessions = read_json(second / "session_manifest.json")["sessions"]

    assert len(first_sessions) == 2
    assert {item["camera_id"] for item in first_sessions} == {"camera-01", "camera-02"}
    assert [item["session_id"] for item in first_sessions] == [
        item["session_id"] for item in second_sessions
    ]


def test_duplicate_hash_is_reported_and_only_one_clip_is_sessionized(
    tmp_path: Path,
) -> None:
    root = tmp_path / "videos"
    _video(root / "camera-01" / "20260819_090000.mp4", "same")
    _video(root / "camera-01" / "20260819_090500.mp4", "same")

    output = scan_video_folder(root, tmp_path / "scan", duration_probe=_probe)
    inventory = read_jsonl(output / "video_inventory.jsonl")

    assert [item["status"] for item in inventory] == ["accepted", "duplicate"]
    assert read_json(output / "session_manifest.json")["sessions"][0]["clip_count"] == 1
    assert "duplicate" in (output / "scan_errors.csv").read_text(encoding="utf-8-sig")


def test_flat_folder_requires_camera_identity(tmp_path: Path) -> None:
    root = tmp_path / "videos"
    _video(root / "20260819_090000.mp4", "one")

    with pytest.raises(AutoLabelingError, match="camera-id"):
        scan_video_folder(root, tmp_path / "scan", duration_probe=_probe)


def test_partition_creates_disjoint_dataset_and_evaluation_manifests(
    tmp_path: Path,
) -> None:
    root = tmp_path / "videos"
    _video(root / "camera-01" / "20260819_090000.mp4", "dataset")
    _video(root / "camera-01" / "20260819_091600.mp4", "benchmark")
    scan_dir = scan_video_folder(root, tmp_path / "scan", duration_probe=_probe)
    assignments = scan_dir / "session_assignments.csv"
    with assignments.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
        fields = list(rows[0])
    for index, row in enumerate(rows):
        row["role"] = "dataset" if index == 0 else "benchmark"
        row["approval_reference"] = f"approval-{index}"
        row["retention_expires_at"] = "2099-01-01T00:00:00+09:00"
        row["subject_category"] = "student"
        row["evaluation_scope"] = "" if index == 0 else "benchmark"
    with assignments.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    with pytest.raises(AutoLabelingError, match="allow-approved-student-data"):
        partition_sessions(scan_dir, assignments, tmp_path / "rejected")

    output = partition_sessions(
        scan_dir,
        assignments,
        tmp_path / "partition",
        allow_approved_student_data=True,
    )
    dataset = read_json(output / "dataset_manifest.json")
    evaluation = read_json(output / "evaluation_manifest.json")
    leak_check = read_json(output / "leak_check.json")

    assert {source["requested_split"] for source in dataset["sources"]} == {"train"}
    assert {source["evaluation_scope"] for source in evaluation["sources"]} == {
        "benchmark"
    }
    assert leak_check["passed"] is True
    assert {source["source_sha256"] for source in dataset["sources"]}.isdisjoint(
        {source["source_sha256"] for source in evaluation["sources"]}
    )


def test_partition_validation_extension_creates_val_only_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "videos"
    _video(root / "camera-01" / "20260819_090000.mp4", "validation")
    _video(root / "camera-01" / "20260819_091600.mp4", "excluded")
    scan_dir = scan_video_folder(root, tmp_path / "scan", duration_probe=_probe)
    assignments = scan_dir / "session_assignments.csv"
    with assignments.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
        fields = list(rows[0])
    rows[0]["role"] = "dataset"
    rows[0]["requested_split"] = "val"
    rows[0]["approval_reference"] = "approval-val"
    rows[0]["retention_expires_at"] = "2099-01-01T00:00:00+09:00"
    rows[0]["subject_category"] = "student"
    rows[1]["role"] = "excluded"
    with assignments.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    base_export = tmp_path / "colab-export-v001"
    base_export.mkdir()
    (base_export / "manifest.json").write_text(
        '{"items":[{"split":"train"}]}', encoding="utf-8"
    )
    monkeypatch.setattr(
        "auto_labeling.privacy.validate_privacy_export",
        lambda path: {
            "status": "valid",
            "image_count": 1,
            "source_dataset_version": "person-v0001",
        },
    )

    output = partition_validation_extension(
        scan_dir,
        assignments,
        tmp_path / "partition-val",
        base_export,
        allow_approved_student_data=True,
    )

    dataset = read_json(output / "dataset_manifest.json")
    receipt = read_json(output / "extension_receipt.json")
    assert {source["requested_split"] for source in dataset["sources"]} == {"val"}
    assert receipt["base_train_count"] == 1
    assert receipt["base_val_count"] == 0
    assert len(receipt["selected_val_sessions"]) == 1


def test_manual_session_override_can_merge_automatic_groups(tmp_path: Path) -> None:
    root = tmp_path / "videos"
    first = _video(root / "camera-01" / "20260819_090000.mp4", "first")
    second = _video(root / "camera-01" / "20260819_091600.mp4", "second")
    override = tmp_path / "session_overrides.csv"
    override.write_text(
        "relative_path,manual_session_id\n"
        f"{first.relative_to(root).as_posix()},lecture-a\n"
        f"{second.relative_to(root).as_posix()},lecture-a\n",
        encoding="utf-8",
    )

    output = scan_video_folder(
        root,
        tmp_path / "scan",
        duration_probe=_probe,
        session_overrides_path=override,
    )

    sessions = read_json(output / "session_manifest.json")["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "lecture-a"
    assert sessions[0]["clip_count"] == 2
