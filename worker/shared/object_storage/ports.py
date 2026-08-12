"""객체 저장소 경계.

프로세스 밖으로 나가는 I/O라 포트를 둔다. 결정 0004가 "MinIO SDK는 어댑터에만
둔다"고 정한 경계이기도 하다. 이 포트 덕분에 MinIO 서버 없이도 적재 경로 전체를
검증할 수 있다.

**S3 호환 범위 안에서만 쓴다.** MinIO 고유 기능에 의존하면 나중에 실제 S3나 다른
호환 저장소로 옮길 수 없다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class StoredObject:
    """저장소에 있는 객체 하나."""

    key: str
    size_bytes: int
    last_modified: datetime


class ObjectStorage(Protocol):
    """영상 세그먼트와 탐지 스냅샷을 담는 저장소.

    구현체는 이 Protocol을 상속하지 않는다. 구조만 맞으면 된다.
    """

    def put_object(
        self, key: str, source_path: Path, *, content_type: str = "video/mp4"
    ) -> StoredObject:
        """파일을 객체로 올린다. 같은 키가 있으면 덮어쓴다."""
        ...

    def put_bytes(self, key: str, data: bytes, *, content_type: str) -> StoredObject:
        """메모리에 있는 바이트를 객체로 올린다. 같은 키가 있으면 덮어쓴다.

        스냅샷은 프레임을 인코딩한 결과가 메모리에 있다. 파일 경로만 받으면
        적재마다 임시 파일을 만들고 지워야 해서 프레임마다 디스크 쓰기가 생긴다.
        """
        ...

    def list_objects(self, prefix: str = "") -> Iterable[StoredObject]:
        """접두사로 객체를 훑는다."""
        ...

    def remove_object(self, key: str) -> None:
        """객체를 지운다. 없는 키는 성공으로 본다."""
        ...
