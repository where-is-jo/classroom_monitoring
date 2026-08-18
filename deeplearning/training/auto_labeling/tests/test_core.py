from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from auto_labeling.core import load_input_manifest, stable_frame_id
from auto_labeling.errors import AutoLabelingError


def _write_manifest(tmp_path: Path, *, subject_category: str, expires_at: str) -> Path:
    video_path = tmp_path / "approved.mp4"
    video_path.write_bytes(b"not-decoded-in-this-test")
    manifest_path = tmp_path / "input.json"
    manifest_path.write_text(
        json.dumps(
            {
                "run_id": "run-core-test",
                "sources": [
                    {
                        "source_id": "source-001",
                        "file_path": "approved.mp4",
                        "approval_reference": "approval-001",
                        "consent_scope": "person-detection-training",
                        "retention_expires_at": expires_at,
                        "camera_id": "camera-001",
                        "session_id": "session-001",
                        "captured_at": "2026-08-18T09:00:00+09:00",
                        "subject_category": subject_category,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest_path


def test_manifest_accepts_consented_adult_mp4(tmp_path: Path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        subject_category="consenting-adult",
        expires_at="2099-01-01T00:00:00+00:00",
    )

    manifest = load_input_manifest(manifest_path, now=datetime(2026, 8, 18, tzinfo=UTC))

    assert manifest.run_id == "run-core-test"
    assert manifest.sources[0].file_path.name == "approved.mp4"


def test_manifest_rejects_student_video(tmp_path: Path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        subject_category="student",
        expires_at="2099-01-01T00:00:00+00:00",
    )

    with pytest.raises(AutoLabelingError, match="실제 학생 영상"):
        load_input_manifest(manifest_path)


def test_manifest_rejects_expired_retention(tmp_path: Path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        subject_category="synthetic",
        expires_at="2020-01-01T00:00:00+00:00",
    )

    with pytest.raises(AutoLabelingError, match="보존 만료"):
        load_input_manifest(manifest_path)


def test_manifest_rejects_missing_approval_reference(tmp_path: Path) -> None:
    manifest_path = _write_manifest(
        tmp_path,
        subject_category="synthetic",
        expires_at="2099-01-01T00:00:00+00:00",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["sources"][0]["approval_reference"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(AutoLabelingError, match="approval_reference"):
        load_input_manifest(manifest_path)


def test_frame_id_is_stable_and_policy_scoped() -> None:
    first = stable_frame_id("a" * 64, 2000, "policy-v1")
    same = stable_frame_id("a" * 64, 2000, "policy-v1")
    changed = stable_frame_id("a" * 64, 2000, "policy-v2")

    assert first == same
    assert first != changed
    assert len(first) == 24
