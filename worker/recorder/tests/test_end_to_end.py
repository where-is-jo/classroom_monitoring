"""세그먼트 파일 → 적재 → 보존 기간 삭제까지 실제 컴포넌트로 잇는 통합 테스트.

대역으로 바꾼 것은 FFmpeg 하나뿐이다. 세그먼트 파일을 직접 만들어 그 뒤 경로를
실제 SegmentUploader·LocalObjectStorage·RetentionPolicy로 검증한다.
MinIO 서버가 없어도 적재 경로 전체가 돌아가는지 확인하려는 것이다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from shared.object_storage import LocalObjectStorage
from ..retention import RetentionPolicy
from ..uploader import SegmentUploader
from .conftest import PLAYABLE_MP4, write_segment

# 완성 판정은 파일 mtime과 현재 시각을 비교한다. 고정된 가짜 시각을 쓰면 방금 만든
# 파일이 몇 시간 지난 것으로 읽혀 판정이 뒤집힌다. 여기서는 실제 시각을 쓴다.


def build_uploader(
    segment_dir: Path, storage: LocalObjectStorage, *, camera_id: str = "camera-01"
) -> SegmentUploader:
    return SegmentUploader(
        camera_id=camera_id,
        segment_dir=segment_dir,
        storage=storage,
        stale_after_seconds=900,
        now=lambda: datetime.now(UTC),
    )


def test_녹화본이_객체_저장소로_옮겨지고_로컬에서_사라진다(tmp_path: Path) -> None:
    segment_dir = tmp_path / "segments" / "camera-01"
    storage = LocalObjectStorage(tmp_path / "objects")

    first = write_segment(segment_dir, datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC), content=PLAYABLE_MP4 + b"aaa")
    second = write_segment(segment_dir, datetime(2026, 8, 10, 9, 10, 0, tzinfo=UTC), content=PLAYABLE_MP4 + b"bbb")
    # 마지막 파일은 FFmpeg이 쓰는 중이라고 본다.
    writing = write_segment(segment_dir, datetime(2026, 8, 10, 9, 20, 0, tzinfo=UTC))

    result = build_uploader(segment_dir, storage).upload_pending()

    assert result.uploaded == 2
    assert not first.exists()
    assert not second.exists()
    assert writing.exists(), "쓰는 중인 세그먼트는 남아 있어야 한다"

    keys = [stored.key for stored in storage.list_objects()]
    assert keys == [
        "camera-01/2026-08-10/20260810T090000Z.mp4",
        "camera-01/2026-08-10/20260810T091000Z.mp4",
    ]
    assert (storage.root_dir / keys[0]).read_bytes() == PLAYABLE_MP4 + b"aaa"


def test_보존_기간이_지난_녹화본이_지워진다(tmp_path: Path) -> None:
    import os

    storage = LocalObjectStorage(tmp_path / "objects")
    segment_dir = tmp_path / "segments" / "camera-01"

    write_segment(segment_dir, datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC))
    write_segment(segment_dir, datetime(2026, 8, 10, 9, 20, 0, tzinfo=UTC))
    build_uploader(segment_dir, storage).upload_pending()

    stored_key = "camera-01/2026-08-10/20260810T090000Z.mp4"
    stored_path = storage.root_dir / stored_key
    # 적재된 객체를 40일 전으로 되돌린다.
    old = (datetime.now(UTC) - timedelta(days=40)).timestamp()
    os.utime(stored_path, (old, old))

    result = RetentionPolicy(storage=storage, retention_days=30).purge()

    assert result.removed == 1
    assert not stored_path.exists()
    assert list(storage.list_objects()) == []


def test_보존_기간_안의_녹화본은_남는다(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path / "objects")
    segment_dir = tmp_path / "segments" / "camera-01"

    write_segment(segment_dir, datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC))
    write_segment(segment_dir, datetime(2026, 8, 10, 9, 20, 0, tzinfo=UTC))
    build_uploader(segment_dir, storage).upload_pending()

    result = RetentionPolicy(storage=storage, retention_days=30).purge()

    assert result.removed == 0
    assert len(list(storage.list_objects())) == 1


def test_카메라가_여럿이어도_객체가_섞이지_않는다(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path / "objects")

    for camera_id in ("camera-01", "camera-02"):
        segment_dir = tmp_path / "segments" / camera_id
        write_segment(segment_dir, datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC))
        write_segment(segment_dir, datetime(2026, 8, 10, 9, 20, 0, tzinfo=UTC))
        build_uploader(segment_dir, storage, camera_id=camera_id).upload_pending()

    assert [stored.key for stored in storage.list_objects("camera-02/")] == [
        "camera-02/2026-08-10/20260810T090000Z.mp4"
    ]
    assert len(list(storage.list_objects())) == 2
