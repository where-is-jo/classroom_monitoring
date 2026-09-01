from pathlib import Path

import numpy as np
import pytest

from auto_labeling import prepare
from auto_labeling.core import SourceInput, load_settings
from auto_labeling.errors import AutoLabelingError


def test_directory_publish_retries_transient_windows_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    attempts = 0
    delays: list[float] = []
    original_replace = Path.replace

    def flaky_replace(path: Path, destination: Path) -> Path:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("temporarily locked")
        return original_replace(path, destination)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    monkeypatch.setattr(prepare.time, "sleep", delays.append)

    prepare._replace_directory_with_retry(source, target)

    assert attempts == 3
    assert delays == [0.25, 0.5]
    assert target.is_dir()


def test_directory_publish_rejects_concurrent_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()

    def locked_replace(_path: Path, _destination: Path) -> Path:
        raise PermissionError("target exists")

    monkeypatch.setattr(Path, "replace", locked_replace)

    with pytest.raises(AutoLabelingError, match="동시에 생성"):
        prepare._replace_directory_with_retry(source, target)


def test_extract_source_rejects_early_decoder_termination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class TruncatedCapture:
        def __init__(self) -> None:
            self.read_count = 0

        def isOpened(self) -> bool:
            return True

        def get(self, key: int) -> float:
            if key == prepare.cv2.CAP_PROP_FPS:
                return 10.0
            if key == prepare.cv2.CAP_PROP_FRAME_COUNT:
                return 100.0
            return 0.0

        def read(self) -> tuple[bool, np.ndarray | None]:
            self.read_count += 1
            if self.read_count > 10:
                return False, None
            return True, np.full((48, 64, 3), 96, dtype=np.uint8)

        def release(self) -> None:
            pass

    monkeypatch.setattr(prepare.cv2, "VideoCapture", lambda _path: TruncatedCapture())
    source = SourceInput(
        source_id="source-001",
        file_path=tmp_path / "truncated.mp4",
        approval_reference="approval-001",
        consent_scope="person-detection-training",
        retention_expires_at="2099-01-01T00:00:00+00:00",
        camera_id="camera-01",
        session_id="session-001",
        captured_at="2026-08-25T00:00:00+09:00",
        subject_category="synthetic",
        usage="dataset",
        requested_split="train",
    )
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()

    with pytest.raises(AutoLabelingError, match="끝까지 도달"):
        prepare._extract_source(source, "f" * 64, frames_dir, load_settings())
