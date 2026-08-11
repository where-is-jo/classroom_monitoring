"""보존 기간이 지난 영상을 지운다.

영상에는 사무실 구성원의 얼굴이 담긴다. 지우는 코드가 없으면 보존 기간을 정해도
지켜지지 않는다.

**보존 기간은 아직 팀이 합의한 값이 아니다**(결정 0004). 설정으로 두되, 시작할 때
현재 값을 로그로 드러내 합의 없이 운영에 쓰이는 것을 눈에 띄게 한다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .errors import ObjectStorageError
from .ports import ObjectStorage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PurgeResult:
    removed: int
    failed: int
    inspected: int


class RetentionPolicy:
    """보존 기간을 넘긴 객체를 지운다."""

    def __init__(
        self,
        *,
        storage: ObjectStorage,
        retention_days: int,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if retention_days < 1:
            raise ValueError("보존 기간은 1일 이상이어야 합니다.")
        self._storage = storage
        self._retention_days = retention_days
        self._now = now

    @property
    def retention_days(self) -> int:
        return self._retention_days

    def purge(self, prefix: str = "") -> PurgeResult:
        """기한이 지난 객체를 지우고 결과를 돌려준다."""
        cutoff = self._now() - timedelta(days=self._retention_days)

        removed = 0
        failed = 0
        inspected = 0
        try:
            candidates = list(self._storage.list_objects(prefix))
        except ObjectStorageError as error:
            logger.error("보존 기간 정리를 위해 목록을 읽지 못했다: %s", error)
            return PurgeResult(removed=0, failed=0, inspected=0)

        for stored in candidates:
            inspected += 1
            if stored.last_modified is None or stored.last_modified > cutoff:
                continue
            try:
                self._storage.remove_object(stored.key)
            except ObjectStorageError as error:
                # 한 객체가 안 지워져도 나머지는 계속 지운다. 실패는 로그로 남긴다.
                failed += 1
                logger.error("보존 기간이 지난 객체를 지우지 못했다: %s", error)
                continue
            removed += 1
            logger.info("보존 기간(%d일)이 지나 삭제: %s", self._retention_days, stored.key)

        if removed or failed:
            logger.info(
                "보존 기간 정리 완료 — 검사 %d, 삭제 %d, 실패 %d",
                inspected,
                removed,
                failed,
            )
        return PurgeResult(removed=removed, failed=failed, inspected=inspected)
