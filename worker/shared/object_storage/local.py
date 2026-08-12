"""로컬 디렉터리를 객체 저장소처럼 쓰는 어댑터.

MinIO 서버가 없는 개발 환경에서 적재 경로 전체를 돌려보기 위한 것이다.
**운영 보관 수단이 아니다.** 결정 0004가 로컬 파일 시스템을 기각한 이유(인스턴스가
늘면 파일 위치가 갈리고 보존 기간 자동화를 직접 구현해야 한다)가 그대로 적용된다.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from .errors import ObjectStorageError
from .ports import StoredObject


class LocalObjectStorage:
    """객체 키를 디렉터리 경로로 그대로 옮겨 저장한다."""

    def __init__(self, root_dir: Path) -> None:
        self._root_dir = root_dir

    @property
    def root_dir(self) -> Path:
        return self._root_dir

    def put_object(
        self, key: str, source_path: Path, *, content_type: str = "video/mp4"
    ) -> StoredObject:
        # content_type은 받기만 한다. 파일 시스템에는 담을 곳이 없고, 포트 서명을
        # MinIO 어댑터와 맞추기 위해 인자로 둔다.
        del content_type
        target_path = self._resolve(key)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(source_path, target_path)
        except OSError as error:
            raise ObjectStorageError(f"객체를 저장하지 못했습니다: {key} ({error})") from error

        return self._stat(key, target_path)

    def put_bytes(self, key: str, data: bytes, *, content_type: str) -> StoredObject:
        del content_type
        target_path = self._resolve(key)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            target_path.write_bytes(data)
        except OSError as error:
            raise ObjectStorageError(f"객체를 저장하지 못했습니다: {key} ({error})") from error

        return self._stat(key, target_path)

    def _stat(self, key: str, target_path: Path) -> StoredObject:
        stat = target_path.stat()
        return StoredObject(
            key=key,
            size_bytes=stat.st_size,
            last_modified=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
        )

    def list_objects(self, prefix: str = "") -> Iterator[StoredObject]:
        if not self._root_dir.exists():
            return

        for path in sorted(self._root_dir.rglob("*")):
            if not path.is_file():
                continue
            key = path.relative_to(self._root_dir).as_posix()
            if not key.startswith(prefix):
                continue
            stat = path.stat()
            yield StoredObject(
                key=key,
                size_bytes=stat.st_size,
                last_modified=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
            )

    def remove_object(self, key: str) -> None:
        target_path = self._resolve(key)
        try:
            target_path.unlink(missing_ok=True)
        except OSError as error:
            raise ObjectStorageError(f"객체를 지우지 못했습니다: {key} ({error})") from error

        # 날짜 디렉터리가 비면 함께 정리한다. 빈 디렉터리가 쌓이면 목록이 지저분해진다.
        root = self._root_dir.resolve()
        for parent in target_path.parents:
            if parent == root or not parent.is_relative_to(root):
                break
            # 없는 객체를 지우는 것은 성공으로 보므로 경로가 없을 수 있다.
            if not parent.is_dir():
                continue
            if any(parent.iterdir()):
                break
            parent.rmdir()

    def _resolve(self, key: str) -> Path:
        target_path = (self._root_dir / key).resolve()
        root = self._root_dir.resolve()
        # 키에 ".."이 섞여 루트 밖으로 나가는 것을 막는다. 키는 카메라 식별자에서
        # 오지만, 경로를 만드는 코드에서 한 번 더 확인한다.
        if not target_path.is_relative_to(root):
            raise ObjectStorageError(f"저장소 루트를 벗어나는 객체 키입니다: {key}")
        return target_path
