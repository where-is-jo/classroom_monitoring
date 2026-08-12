"""스냅샷 객체 저장소 포트.

프로세스 밖으로 나가는 I/O라 포트를 둔다. 결정 0004가 정한 네 경계 중 "객체 저장소"이며,
**fastapi에서 이 경계를 구현하는 것은 이번이 처음이다.**

MinIO SDK는 어댑터에만 둔다. 서비스 계층은 이 Protocol만 본다.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class StoredObject:
    """저장소에 있는 객체 하나. 키 해석 전의 날것이다."""

    key: str
    size_bytes: int
    last_modified: datetime


@dataclass(frozen=True)
class ObjectContent:
    """내려받은 객체의 내용."""

    data: bytes
    content_type: str


class SnapshotStorage(Protocol):
    """스냅샷을 담고 있는 저장소. 읽기만 한다.

    **fastapi는 스냅샷을 만들지도 지우지도 않는다.** 적재는 worker가, 삭제는 저장소의
    lifecycle 규칙이 한다(결정 0011).
    """

    def list_objects(self, prefix: str = "") -> Iterator[StoredObject]:
        """접두사로 객체를 훑는다."""
        ...

    def get_object(self, key: str) -> ObjectContent | None:
        """객체 내용을 가져온다. 없으면 None."""
        ...
