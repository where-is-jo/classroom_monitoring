"""메모리 스냅샷 저장소.

MinIO 없이 화면과 테스트를 돌리기 위한 것이다. **운영 수단이 아니다.**
`OBJECT_STORAGE_BACKEND`가 설정되지 않은 local 개발에서 빈 목록을 돌려준다.
"""

from __future__ import annotations

from collections.abc import Iterator

from ..ports import ObjectContent, StoredObject


class InMemorySnapshotStorage:
    def __init__(self, objects: dict[str, tuple[StoredObject, bytes]] | None = None) -> None:
        self._objects = objects or {}

    def list_objects(self, prefix: str = "") -> Iterator[StoredObject]:
        for key, (stored, _) in sorted(self._objects.items()):
            if key.startswith(prefix):
                yield stored

    def get_object(self, key: str) -> ObjectContent | None:
        entry = self._objects.get(key)
        if entry is None:
            return None
        return ObjectContent(data=entry[1], content_type="image/jpeg")
