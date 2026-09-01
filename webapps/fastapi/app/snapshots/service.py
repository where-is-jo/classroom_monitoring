"""스냅샷 목록과 내용 조회.

**FastAPI에 의존하지 않는다.** Request도 HTTPException도 쓰지 않고 포트만 본다.
"""

from __future__ import annotations

import logging
from datetime import date

from .errors import SnapshotNotFoundError, SnapshotStorageUnavailableError
from .models import Snapshot, SnapshotPage, parse_snapshot_key
from .ports import ObjectContent, SnapshotStorage

logger = logging.getLogger(__name__)

__all__ = ["SnapshotService"]


class SnapshotService:
    def __init__(self, storage: SnapshotStorage, *, page_size_max: int = 200) -> None:
        self._storage = storage
        self._page_size_max = page_size_max

    def list_snapshots(
        self, *, camera_id: str | None = None, limit: int = 50, offset: int = 0
    ) -> SnapshotPage:
        """최근 촬영 순으로 스냅샷을 돌려준다.

        저장소는 키 순서(카메라 → 날짜 → 시각)로 훑기 때문에 최신순으로 보려면
        전체를 모아 정렬해야 한다. **객체가 많아지면 이 방식이 느려진다** — 지금은
        메타데이터 저장소가 따로 없어서 이렇게 두고(결정 0011), 느려지면 그때
        메타데이터 전달 방식을 정한다.
        """
        limit = min(limit, self._page_size_max)
        prefix = f"{camera_id}/" if camera_id else ""

        snapshots: list[Snapshot] = []
        try:
            for stored in self._storage.list_objects(prefix):
                parsed = parse_snapshot_key(stored.key)
                if parsed is None:
                    # 규칙에 맞지 않는 객체가 섞여도 목록 전체를 실패시키지 않는다.
                    logger.debug("스냅샷 키 규칙에 맞지 않아 건너뛴다: %s", stored.key)
                    continue
                parsed_camera_id, captured_at = parsed
                snapshots.append(
                    Snapshot(
                        key=stored.key,
                        camera_id=parsed_camera_id,
                        captured_at=captured_at,
                        size_bytes=stored.size_bytes,
                    )
                )
        except SnapshotStorageUnavailableError:
            raise
        except Exception as error:
            # 어댑터가 놓친 예외도 도메인 오류로 바꾼다. 화면에 500이 그대로 나가면
            # 저장소 문제인지 앱 문제인지 구분되지 않는다.
            logger.warning("스냅샷 목록을 읽지 못했다: %s", error)
            raise SnapshotStorageUnavailableError() from error

        snapshots.sort(key=lambda item: item.captured_at, reverse=True)
        return SnapshotPage(
            items=snapshots[offset : offset + limit],
            total=len(snapshots),
            limit=limit,
            offset=offset,
        )

    def existing_keys(self, camera_id: str, day: date) -> frozenset[str]:
        """한 카메라의 하루치 스냅샷 키 집합.

        탐지 이벤트에서 계산한 키(`build_snapshot_key`)가 실제 객체인지 확인하는 데
        쓴다. **`list_snapshots`와 달리 하루치 접두사만 훑는다** — 전체를 모아 정렬하지
        않으므로 조회 범위가 며칠로 늘어나도 비용이 날짜 수에 비례하는 데서 그친다.

        키만 필요하므로 규칙에 맞지 않는 객체도 그대로 담는다. 어차피 계산한 키와
        비교할 뿐이라 걸러낼 이유가 없다.
        """
        prefix = f"{camera_id}/{day.isoformat()}/"
        try:
            return frozenset(stored.key for stored in self._storage.list_objects(prefix))
        except SnapshotStorageUnavailableError:
            raise
        except Exception as error:
            logger.warning("스냅샷 키 목록을 읽지 못했다: %s", error)
            raise SnapshotStorageUnavailableError() from error

    def get_image(self, key: str) -> ObjectContent:
        """스냅샷 이미지를 가져온다.

        **fastapi가 바이트를 대신 전달한다.** presigned URL로 브라우저를 저장소에
        직접 붙이면 "브라우저는 fastapi만 호출한다"는 규칙이 깨진다.
        """
        if parse_snapshot_key(key) is None:
            # 키 규칙 검사가 경로 조작을 막는 역할도 한다.
            raise SnapshotNotFoundError()

        try:
            content = self._storage.get_object(key)
        except SnapshotStorageUnavailableError:
            raise
        except Exception as error:
            logger.warning("스냅샷을 읽지 못했다: %s", error)
            raise SnapshotStorageUnavailableError() from error

        if content is None:
            raise SnapshotNotFoundError()
        return content

    def camera_options(self) -> list[str]:
        """목록 화면의 카메라 필터 선택지."""
        page = self.list_snapshots(limit=self._page_size_max)
        return sorted({item.camera_id for item in page.items})
