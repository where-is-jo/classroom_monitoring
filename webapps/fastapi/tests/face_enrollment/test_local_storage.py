from __future__ import annotations

import io
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

from app.face_enrollment.adapters.local_storage import LocalFaceObjectStorage
from app.face_enrollment.models import FaceAnalysis, FaceSampleMetadata, PoseBin


def sample_metadata() -> FaceSampleMetadata:
    return FaceSampleMetadata(
        sample_id="student-01_front_000001",
        pose=PoseBin.FRONT,
        captured_at=datetime(2026, 8, 12, 15, 30, 46, tzinfo=UTC),
        analysis=FaceAnalysis(1, 0.99, 0.3, True, 0, 0, 0, 0.9, 0.9, 0.99, 0, 0, 0),
    )


def synthetic_jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (100, 140, 180)).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_local_storage_writes_and_deletes_enrollment(tmp_path: Path) -> None:
    storage = LocalFaceObjectStorage(tmp_path)
    enrollment_id = "12345678-1234-1234-1234-123456789abc"
    storage.prepare_enrollment(
        enrollment_id,
        "student 01",
        datetime(2026, 8, 12, 15, 30, 45, tzinfo=UTC),
    )
    folder = tmp_path / "20260812-153045-student-01"
    storage.put_sample(enrollment_id, sample_metadata(), synthetic_jpeg())
    samples = list((folder / "originals").glob("*.jpg"))
    assert len(samples) == 1
    assert samples[0].read_bytes() == synthetic_jpeg()
    storage.delete_enrollment(enrollment_id)
    assert not folder.exists()


def test_local_storage_builds_augmented_dataset_and_manifest(tmp_path: Path) -> None:
    storage = LocalFaceObjectStorage(tmp_path)
    enrollment_id = "12345678-1234-1234-1234-123456789abc"
    storage.prepare_enrollment(
        enrollment_id,
        "student 01",
        datetime(2026, 8, 12, 15, 30, 45, tzinfo=UTC),
    )
    metadata = sample_metadata()
    for index in range(120):
        storage.put_sample(
            enrollment_id,
            replace(metadata, sample_id=f"student-01_front_{index + 1:06d}"),
            synthetic_jpeg(),
        )

    storage.finalize_dataset(enrollment_id, "student 01", 180)

    folder = tmp_path / "20260812-153045-student-01"
    augmented = sorted((folder / "augmented").glob("*.jpg"))
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert len(augmented) == 180
    assert manifest["original_sample_count"] == 120
    assert manifest["augmented_sample_count"] == 180
    assert manifest["total_sample_count"] == 300
    assert all(item["source"] for item in manifest["samples"] if item["kind"] == "augmented")
