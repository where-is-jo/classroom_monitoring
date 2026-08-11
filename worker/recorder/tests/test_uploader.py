"""완성된 세그먼트 판별과 적재 검증."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..uploader import SegmentUploader, find_completed_segments
from .conftest import PLAYABLE_MP4, FakeStorage, segment_name, write_segment

NOW = datetime(2026, 8, 10, 9, 30, 0, tzinfo=UTC)


class TestFindCompletedSegments:
    def test_없는_디렉터리는_빈_목록이다(self, segment_dir: Path) -> None:
        assert find_completed_segments(segment_dir, now=NOW, stale_after_seconds=900) == []

    def test_쓰는_중인_마지막_세그먼트는_제외한다(self, segment_dir: Path) -> None:
        """mp4는 moov atom을 마지막에 붙인다. 미완성 파일을 올리면 재생할 수 없다."""
        write_segment(segment_dir, datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC))
        write_segment(segment_dir, datetime(2026, 8, 10, 9, 10, 0, tzinfo=UTC))
        write_segment(segment_dir, datetime(2026, 8, 10, 9, 20, 0, tzinfo=UTC))

        completed = find_completed_segments(segment_dir, now=NOW, stale_after_seconds=900)

        assert [segment.path.name for segment in completed] == [
            segment_name(datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC)),
            segment_name(datetime(2026, 8, 10, 9, 10, 0, tzinfo=UTC)),
        ]

    def test_오래된_순으로_돌려준다(self, segment_dir: Path) -> None:
        write_segment(segment_dir, datetime(2026, 8, 10, 9, 20, 0, tzinfo=UTC))
        write_segment(segment_dir, datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC))
        write_segment(segment_dir, datetime(2026, 8, 10, 9, 10, 0, tzinfo=UTC))

        completed = find_completed_segments(segment_dir, now=NOW, stale_after_seconds=900)

        assert [segment.recorded_at for segment in completed] == [
            datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC),
            datetime(2026, 8, 10, 9, 10, 0, tzinfo=UTC),
        ]

    def test_한_장뿐이면_아무것도_올리지_않는다(self, segment_dir: Path) -> None:
        write_segment(segment_dir, datetime(2026, 8, 10, 9, 20, 0, tzinfo=UTC))

        assert find_completed_segments(segment_dir, now=NOW, stale_after_seconds=900) == []

    def test_마지막_세그먼트가_멈춰_있으면_완료로_본다(self, segment_dir: Path) -> None:
        """FFmpeg이 죽으면 마지막 세그먼트가 영원히 올라가지 않는다."""
        write_segment(
            segment_dir, datetime(2026, 8, 10, 9, 20, 0, tzinfo=UTC), age_seconds=1000
        )

        completed = find_completed_segments(
            segment_dir, now=datetime.now(UTC), stale_after_seconds=900
        )

        assert len(completed) == 1

    def test_FFmpeg이_만들지_않은_파일은_건드리지_않는다(self, segment_dir: Path) -> None:
        segment_dir.mkdir(parents=True)
        (segment_dir / "메모.txt").write_text("사람이 둔 파일", encoding="utf-8")
        (segment_dir / "recording.mp4").write_bytes(b"x")
        write_segment(segment_dir, datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC))
        write_segment(segment_dir, datetime(2026, 8, 10, 9, 10, 0, tzinfo=UTC))

        completed = find_completed_segments(segment_dir, now=NOW, stale_after_seconds=900)

        assert [segment.path.name for segment in completed] == [
            segment_name(datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC))
        ]


class TestSegmentUploader:
    def _build(
        self, segment_dir: Path, storage: FakeStorage, *, delete: bool = True
    ) -> SegmentUploader:
        return SegmentUploader(
            camera_id="camera-01",
            segment_dir=segment_dir,
            storage=storage,
            stale_after_seconds=900,
            delete_after_upload=delete,
            now=lambda: NOW,
        )

    def test_완성된_세그먼트를_객체_키로_올린다(
        self, segment_dir: Path, storage: FakeStorage
    ) -> None:
        write_segment(segment_dir, datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC))
        write_segment(segment_dir, datetime(2026, 8, 10, 9, 20, 0, tzinfo=UTC))

        result = self._build(segment_dir, storage).upload_pending()

        assert result.uploaded == 1
        assert list(storage.objects) == ["camera-01/2026-08-10/20260810T090000Z.mp4"]

    def test_적재에_성공하면_로컬_파일을_지운다(
        self, segment_dir: Path, storage: FakeStorage
    ) -> None:
        path = write_segment(segment_dir, datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC))
        write_segment(segment_dir, datetime(2026, 8, 10, 9, 20, 0, tzinfo=UTC))

        self._build(segment_dir, storage).upload_pending()

        assert not path.exists()

    def test_적재에_실패하면_로컬_파일을_남긴다(self, segment_dir: Path) -> None:
        """지워버리면 재시도할 수 없고 그 시간대 영상이 사라진다."""
        storage = FakeStorage(fail_keys={"camera-01/2026-08-10/20260810T090000Z.mp4"})
        path = write_segment(segment_dir, datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC))
        write_segment(segment_dir, datetime(2026, 8, 10, 9, 20, 0, tzinfo=UTC))

        result = self._build(segment_dir, storage).upload_pending()

        assert result.failed == 1
        assert result.has_failure
        assert path.exists()

    def test_한_장이_실패해도_나머지는_올린다(self, segment_dir: Path) -> None:
        storage = FakeStorage(fail_keys={"camera-01/2026-08-10/20260810T090000Z.mp4"})
        write_segment(segment_dir, datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC))
        write_segment(segment_dir, datetime(2026, 8, 10, 9, 10, 0, tzinfo=UTC))
        write_segment(segment_dir, datetime(2026, 8, 10, 9, 20, 0, tzinfo=UTC))

        result = self._build(segment_dir, storage).upload_pending()

        assert result.uploaded == 1
        assert result.failed == 1

    def test_다음_주기에_실패한_세그먼트를_다시_시도한다(
        self, segment_dir: Path
    ) -> None:
        storage = FakeStorage(fail_keys={"camera-01/2026-08-10/20260810T090000Z.mp4"})
        write_segment(segment_dir, datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC))
        write_segment(segment_dir, datetime(2026, 8, 10, 9, 20, 0, tzinfo=UTC))
        uploader = self._build(segment_dir, storage)

        uploader.upload_pending()
        storage.fail_keys.clear()
        result = uploader.upload_pending()

        assert result.uploaded == 1
        assert "camera-01/2026-08-10/20260810T090000Z.mp4" in storage.objects

    def test_올릴_것이_없으면_아무것도_하지_않는다(
        self, segment_dir: Path, storage: FakeStorage
    ) -> None:
        result = self._build(segment_dir, storage).upload_pending()

        assert result.uploaded == 0
        assert storage.put_calls == []

    def test_보관_설정이_꺼져_있으면_로컬을_남긴다(
        self, segment_dir: Path, storage: FakeStorage
    ) -> None:
        path = write_segment(segment_dir, datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC))
        write_segment(segment_dir, datetime(2026, 8, 10, 9, 20, 0, tzinfo=UTC))

        self._build(segment_dir, storage, delete=False).upload_pending()

        assert path.exists()

    def test_날짜가_바뀌면_다른_접두사에_들어간다(
        self, segment_dir: Path, storage: FakeStorage
    ) -> None:
        write_segment(segment_dir, datetime(2026, 8, 9, 23, 50, 0, tzinfo=UTC))
        write_segment(segment_dir, datetime(2026, 8, 10, 0, 10, 0, tzinfo=UTC))
        write_segment(segment_dir, datetime(2026, 8, 10, 9, 20, 0, tzinfo=UTC))

        self._build(segment_dir, storage).upload_pending()

        assert sorted(storage.objects) == [
            "camera-01/2026-08-09/20260809T235000Z.mp4",
            "camera-01/2026-08-10/20260810T001000Z.mp4",
        ]


def test_경과_시간_계산에_timedelta를_옳게_쓴다(segment_dir: Path) -> None:
    """하루 넘게 멈춰 있어도 완료로 판단해야 한다."""
    write_segment(
        segment_dir,
        datetime(2026, 8, 9, 9, 0, 0, tzinfo=UTC),
        age_seconds=timedelta(days=2).total_seconds(),
    )

    completed = find_completed_segments(
        segment_dir, now=datetime.now(UTC), stale_after_seconds=900
    )

    assert len(completed) == 1


class TestPlayabilityGuard:
    """FFmpeg이 완성하지 못한 파일을 저장소에 올리지 않는지 본다."""

    def _uploader(self, segment_dir: Path, storage: FakeStorage) -> SegmentUploader:
        return SegmentUploader(
            camera_id="camera-01",
            segment_dir=segment_dir,
            storage=storage,
            stale_after_seconds=900,
            now=lambda: NOW,
        )

    def test_moov가_없으면_올리지_않는다(
        self, segment_dir: Path, storage: FakeStorage
    ) -> None:
        """실제 FFmpeg을 강제 종료했을 때 남는 파일이 이 형태다(ftyp만, 48바이트)."""
        broken = write_segment(
            segment_dir,
            datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC),
            content=(32).to_bytes(4, "big") + b"ftyp" + b"isom" + bytes(24),
        )
        write_segment(segment_dir, datetime(2026, 8, 10, 9, 20, 0, tzinfo=UTC))

        result = self._uploader(segment_dir, storage).upload_pending()

        assert result.skipped == 1
        assert result.uploaded == 0
        assert storage.put_calls == []
        assert broken.exists(), "사람이 확인할 수 있게 로컬에 남긴다"

    def test_moov가_있으면_올린다(
        self, segment_dir: Path, storage: FakeStorage
    ) -> None:
        write_segment(
            segment_dir,
            datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC),
            content=PLAYABLE_MP4,
        )
        write_segment(segment_dir, datetime(2026, 8, 10, 9, 20, 0, tzinfo=UTC))

        result = self._uploader(segment_dir, storage).upload_pending()

        assert result.uploaded == 1
        assert result.skipped == 0

    def test_빈_파일을_올리지_않는다(
        self, segment_dir: Path, storage: FakeStorage
    ) -> None:
        write_segment(segment_dir, datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC), content=b"")
        write_segment(segment_dir, datetime(2026, 8, 10, 9, 20, 0, tzinfo=UTC))

        assert self._uploader(segment_dir, storage).upload_pending().uploaded == 0


class TestIncludeInProgress:
    def test_종료할_때는_마지막_세그먼트까지_올린다(
        self, segment_dir: Path, storage: FakeStorage
    ) -> None:
        """FFmpeg을 세운 뒤에는 쓰는 중인 파일이 없다. 안 올리면 로컬에만 남는다."""
        content = PLAYABLE_MP4
        write_segment(segment_dir, datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC), content=content)
        write_segment(segment_dir, datetime(2026, 8, 10, 9, 20, 0, tzinfo=UTC), content=content)

        uploader = SegmentUploader(
            camera_id="camera-01", segment_dir=segment_dir, storage=storage,
            stale_after_seconds=900, now=lambda: NOW,
        )

        assert uploader.upload_pending().uploaded == 1
        assert uploader.upload_pending(include_in_progress=True).uploaded == 1
        assert len(storage.objects) == 2
