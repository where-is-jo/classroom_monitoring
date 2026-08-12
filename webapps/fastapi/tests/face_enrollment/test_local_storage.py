from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.face_enrollment.adapters.local_storage import LocalFaceObjectStorage


def test_local_storage_writes_and_deletes_enrollment(tmp_path: Path) -> None:
    storage = LocalFaceObjectStorage(tmp_path)
    enrollment_id = "12345678-1234-1234-1234-123456789abc"
    storage.prepare_enrollment(
        enrollment_id,
        "student 01",
        datetime(2026, 8, 12, 15, 30, 45, tzinfo=UTC),
    )
    folder = tmp_path / "20260812-153045-student-01"
    storage.put_sample(enrollment_id, "student-01_front_000001", b"jpeg")
    samples = list(folder.glob("*.jpg"))
    assert len(samples) == 1
    assert samples[0].read_bytes() == b"jpeg"
    storage.delete_enrollment(enrollment_id)
    assert not folder.exists()
