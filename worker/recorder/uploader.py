"""완성된 세그먼트를 골라 객체 저장소에 적재한다.

**쓰는 중인 파일을 올리지 않는다.** FFmpeg은 가장 최근 세그먼트에 계속 쓰고 있고,
mp4는 moov atom을 마지막에 붙이기 때문에 그 파일을 올리면 재생할 수 없는 객체가
저장소에 남는다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .errors import ObjectStorageError
from .object_keys import build_object_key
from .ports import ObjectStorage
from .segmenter import parse_segment_recorded_at

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SegmentFile:
    """디스크에 있는 세그먼트 하나."""

    path: Path
    recorded_at: datetime


@dataclass(frozen=True)
class UploadResult:
    uploaded: int
    failed: int
    skipped: int

    @property
    def has_failure(self) -> bool:
        return self.failed > 0


def find_completed_segments(
    segment_dir: Path,
    *,
    now: datetime,
    stale_after_seconds: float,
) -> list[SegmentFile]:
    """녹화가 끝난 세그먼트만 골라 오래된 순으로 돌려준다.

    가장 최근 파일은 FFmpeg이 아직 쓰고 있다고 보고 제외한다. 다만 그 파일의
    수정 시각이 `stale_after_seconds`보다 오래됐으면 FFmpeg이 죽은 것이므로
    포함한다. 그러지 않으면 마지막 세그먼트가 영원히 올라가지 않는다.
    """
    if not segment_dir.exists():
        return []

    segments: list[SegmentFile] = []
    for path in segment_dir.iterdir():
        if not path.is_file():
            continue
        recorded_at = parse_segment_recorded_at(path)
        if recorded_at is None:
            # FFmpeg이 만든 파일이 아니다. 건드리지 않는다.
            continue
        segments.append(SegmentFile(path=path, recorded_at=recorded_at))

    if not segments:
        return []

    segments.sort(key=lambda segment: segment.recorded_at)
    newest = segments[-1]
    try:
        modified_at = datetime.fromtimestamp(newest.path.stat().st_mtime, tz=UTC)
    except OSError:
        return segments[:-1]

    is_stale = (now - modified_at).total_seconds() >= stale_after_seconds
    if is_stale:
        logger.info(
            "마지막 세그먼트가 %.0f초 동안 변하지 않아 완료된 것으로 본다: %s",
            stale_after_seconds,
            newest.path.name,
        )
        return segments
    return segments[:-1]


class SegmentUploader:
    """한 카메라의 세그먼트를 저장소로 옮긴다."""

    def __init__(
        self,
        *,
        camera_id: str,
        segment_dir: Path,
        storage: ObjectStorage,
        stale_after_seconds: float,
        delete_after_upload: bool = True,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._camera_id = camera_id
        self._segment_dir = segment_dir
        self._storage = storage
        self._stale_after_seconds = stale_after_seconds
        self._delete_after_upload = delete_after_upload
        self._now = now

    def upload_pending(self) -> UploadResult:
        """올릴 수 있는 세그먼트를 모두 올린다."""
        segments = find_completed_segments(
            self._segment_dir,
            now=self._now(),
            stale_after_seconds=self._stale_after_seconds,
        )

        uploaded = 0
        failed = 0
        for segment in segments:
            if self._upload_one(segment):
                uploaded += 1
            else:
                failed += 1

        return UploadResult(uploaded=uploaded, failed=failed, skipped=0)

    def _upload_one(self, segment: SegmentFile) -> bool:
        key = build_object_key(self._camera_id, segment.recorded_at)
        try:
            stored = self._storage.put_object(key, segment.path)
        except ObjectStorageError as error:
            # 로컬 파일을 지우지 않는다. 다음 주기에 다시 시도한다.
            # 같은 키로 덮어쓰므로 두 번 올라가도 객체가 중복되지 않는다.
            logger.error("카메라 %s 세그먼트 적재 실패: %s", self._camera_id, error)
            return False

        logger.info(
            "카메라 %s 세그먼트 적재: %s (%d bytes)",
            self._camera_id,
            stored.key,
            stored.size_bytes,
        )

        if self._delete_after_upload:
            self._remove_local(segment.path)
        return True

    def _remove_local(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            # 적재는 끝났으므로 실패로 보지 않는다. 다만 디스크가 계속 차므로 알린다.
            logger.warning(
                "카메라 %s 적재 후 로컬 파일을 지우지 못했다: %s (%s)",
                self._camera_id,
                path.name,
                error,
            )
