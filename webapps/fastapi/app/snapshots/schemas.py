"""스냅샷 HTTP 스키마.

응답 래퍼를 쓰지 않는다. 목록은 items/total/limit/offset이고 시각은 ISO 8601 UTC다
(docs/conventions/api-convention.md).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from .models import Snapshot, SnapshotPage


class SnapshotResponse(BaseModel):
    key: str
    camera_id: str
    captured_at: datetime
    size_bytes: int
    image_path: str

    @classmethod
    def from_domain(cls, item: Snapshot) -> SnapshotResponse:
        return cls(
            key=item.key,
            camera_id=item.camera_id,
            captured_at=item.captured_at,
            size_bytes=item.size_bytes,
            image_path=f"/api/v1/snapshots/image/{item.key}",
        )


class SnapshotListResponse(BaseModel):
    items: list[SnapshotResponse]
    total: int
    limit: int
    offset: int

    @classmethod
    def from_domain(cls, page: SnapshotPage) -> SnapshotListResponse:
        return cls(
            items=[SnapshotResponse.from_domain(item) for item in page.items],
            total=page.total,
            limit=page.limit,
            offset=page.offset,
        )
