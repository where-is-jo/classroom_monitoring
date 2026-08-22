from pathlib import Path

import pytest

from auto_labeling import prepare
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
