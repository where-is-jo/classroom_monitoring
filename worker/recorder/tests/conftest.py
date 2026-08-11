"""recorder 테스트 대역."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ..errors import ObjectStorageError
from ..ports import StoredObject

# 최소한의 재생 가능한 mp4 흉내. ftyp와 moov 두 box만 있으면 is_playable_mp4를 통과한다.
# 이스케이프 없이 조립해 소스에 널 바이트가 섞이지 않게 한다.
PLAYABLE_MP4 = (
    (16).to_bytes(4, "big")
    + b"ftyp"
    + b"isom"
    + bytes(4)
    + (16).to_bytes(4, "big")
    + b"moov"
    + bytes(8)
)


class FakeStdin:
    """FFmpeg stdin 파이프 대역. 종료 요청('q')이 왔는지 기록한다."""

    def __init__(self, *, broken: bool = False) -> None:
        self.broken = broken
        self.written = b""
        self.closed = False

    def write(self, data: bytes) -> int:
        if self.broken:
            raise BrokenPipeError("파이프가 닫혔다")
        self.written += data
        return len(data)

    def flush(self) -> None:
        if self.broken:
            raise BrokenPipeError("파이프가 닫혔다")

    def close(self) -> None:
        self.closed = True

    @property
    def quit_requested(self) -> bool:
        return b"q" in self.written


class FakeProcess:
    """subprocess.Popen 대역.

    `ignores_quit=True`면 'q'를 받아도 끝나지 않는다. 강제 종료로 넘어가는
    경로를 검증하기 위한 것이다.
    """

    def __init__(
        self,
        exit_codes: list[int | None] | None = None,
        *,
        ignores_quit: bool = False,
        ignores_terminate: bool = False,
        broken_stdin: bool = False,
    ) -> None:
        self._exit_codes = exit_codes or [None]
        self._poll_index = 0
        self._ignores_quit = ignores_quit
        self._ignores_terminate = ignores_terminate
        self.stdin = FakeStdin(broken=broken_stdin)
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
        if self.killed:
            return 0
        if self.terminated and not self._ignores_terminate:
            return 0
        if self.stdin.quit_requested and not self._ignores_quit:
            return 0
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=timeout or 0)


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


def segment_name(moment: datetime) -> str:
    """FFmpeg이 붙일 파일 이름. 로컬 시각이므로 테스트에 하드코딩하지 않는다."""
    return f"{moment.astimezone():%Y%m%d_%H%M%S}.mp4"


def write_segment(
    segment_dir: Path,
    moment: datetime,
    *,
    content: bytes = PLAYABLE_MP4,
    age_seconds: float = 0,
) -> Path:
    """세그먼트 파일을 만든다. age_seconds만큼 오래된 것으로 mtime을 조정한다.

    `moment`는 시각대가 붙은 값으로 받고, 파일 이름은 FFmpeg이 만드는 것과 같은
    **로컬 시각**으로 적는다. 이렇게 해야 테스트가 시각대 변환까지 함께 검증한다.
    """
    import os

    segment_dir.mkdir(parents=True, exist_ok=True)
    path = segment_dir / segment_name(moment)
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
