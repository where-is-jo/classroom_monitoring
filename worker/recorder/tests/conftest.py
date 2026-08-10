"""recorder 테스트 대역."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ..errors import ObjectStorageError
from ..ports import StoredObject


class FakeProcess:
    def __init__(self, exit_codes: list[int | None] | None = None) -> None:
        self._exit_codes = exit_codes or [None]
        self._poll_index = 0
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        index = min(self._poll_index, len(self._exit_codes) - 1)
        self._poll_index += 1
        return self._exit_codes[index]

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        return 0


class FakeRunner:
    def __init__(self, processes: list[FakeProcess] | None = None) -> None:
        self._processes = processes or [FakeProcess()]
        self.commands: list[list[str]] = []

    def __call__(self, command: Sequence[str]) -> FakeProcess:
        index = min(len(self.commands), len(self._processes) - 1)
        self.commands.append(list(command))
        return self._processes[index]


class FakeStorage:
    """ObjectStorage 대역. 메모리에 키만 들고 있는다."""

    def __init__(self, *, fail_keys: set[str] | None = None) -> None:
        self.objects: dict[str, StoredObject] = {}
        self.fail_keys = fail_keys or set()
        self.put_calls: list[tuple[str, Path]] = []
        self.removed: list[str] = []

    def put_object(self, key: str, source_path: Path) -> StoredObject:
        self.put_calls.append((key, source_path))
        if key in self.fail_keys:
            raise ObjectStorageError(f"적재 실패 대역: {key}")
        stored = StoredObject(
            key=key,
            size_bytes=source_path.stat().st_size,
            last_modified=datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC),
        )
        self.objects[key] = stored
        return stored

    def list_objects(self, prefix: str = "") -> Iterator[StoredObject]:
        for key, stored in sorted(self.objects.items()):
            if key.startswith(prefix):
                yield stored

    def remove_object(self, key: str) -> None:
        if key in self.fail_keys:
            raise ObjectStorageError(f"삭제 실패 대역: {key}")
        self.removed.append(key)
        self.objects.pop(key, None)

    def add(self, key: str, *, last_modified: datetime, size_bytes: int = 10) -> None:
        self.objects[key] = StoredObject(
            key=key, size_bytes=size_bytes, last_modified=last_modified
        )


def write_segment(
    segment_dir: Path, moment: datetime, *, content: bytes = b"video", age_seconds: float = 0
) -> Path:
    """세그먼트 파일을 만든다. age_seconds만큼 오래된 것으로 mtime을 조정한다."""
    import os

    segment_dir.mkdir(parents=True, exist_ok=True)
    path = segment_dir / f"{moment.strftime('%Y%m%dT%H%M%SZ')}.mp4"
    path.write_bytes(content)
    if age_seconds:
        past = (datetime.now(UTC) - timedelta(seconds=age_seconds)).timestamp()
        os.utime(path, (past, past))
    return path


@pytest.fixture
def segment_dir(tmp_path: Path) -> Path:
    return tmp_path / "segments"


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()
