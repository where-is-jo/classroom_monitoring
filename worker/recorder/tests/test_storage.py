"""객체 키 규칙과 로컬 어댑터 검증."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from ..adapters.local import LocalObjectStorage
from ..errors import ObjectStorageError
from ..object_keys import build_object_key, camera_prefix


class TestObjectKey:
    def test_카메라_날짜_시각_순으로_만든다(self) -> None:
        key = build_object_key(
            "camera-01", datetime(2026, 8, 10, 9, 5, 30, tzinfo=UTC)
        )

        assert key == "camera-01/2026-08-10/20260810T090530Z.mp4"

    def test_카메라가_맨_앞이라_접두사로_한_대를_고를_수_있다(self) -> None:
        key = build_object_key("camera-02", datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC))

        assert key.startswith(camera_prefix("camera-02"))
        assert not key.startswith(camera_prefix("camera-01"))

    def test_다른_시각대를_UTC로_바꿔_담는다(self) -> None:
        """로컬 시각으로 두면 서버 시각대가 바뀔 때 같은 순간이 두 날짜에 걸린다."""
        kst = timezone(timedelta(hours=9))
        key = build_object_key("camera-01", datetime(2026, 8, 10, 8, 30, 0, tzinfo=kst))

        # KST 08:30 == UTC 전날 23:30
        assert key == "camera-01/2026-08-09/20260809T233000Z.mp4"

    def test_키에_콜론이_들어가지_않는다(self) -> None:
        """콜론은 일부 파일 시스템과 S3 도구에서 다루기 번거롭다."""
        key = build_object_key("camera-01", datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC))

        assert ":" not in key


class TestLocalObjectStorage:
    def _build(self, tmp_path: Path) -> LocalObjectStorage:
        return LocalObjectStorage(tmp_path / "objects")

    def test_객체를_키_경로에_저장한다(self, tmp_path: Path) -> None:
        storage = self._build(tmp_path)
        source = tmp_path / "segment.mp4"
        source.write_bytes(b"video-bytes")

        stored = storage.put_object("camera-01/2026-08-10/a.mp4", source)

        assert stored.size_bytes == len(b"video-bytes")
        assert (storage.root_dir / "camera-01/2026-08-10/a.mp4").read_bytes() == b"video-bytes"

    def test_같은_키를_덮어쓴다(self, tmp_path: Path) -> None:
        """적재 재시도가 객체를 중복시키지 않아야 한다."""
        storage = self._build(tmp_path)
        source = tmp_path / "segment.mp4"
        source.write_bytes(b"first")
        storage.put_object("camera-01/a.mp4", source)
        source.write_bytes(b"second")

        storage.put_object("camera-01/a.mp4", source)

        assert (storage.root_dir / "camera-01/a.mp4").read_bytes() == b"second"
        assert len(list(storage.list_objects())) == 1

    def test_접두사로_훑는다(self, tmp_path: Path) -> None:
        storage = self._build(tmp_path)
        source = tmp_path / "segment.mp4"
        source.write_bytes(b"x")
        storage.put_object("camera-01/2026-08-10/a.mp4", source)
        storage.put_object("camera-02/2026-08-10/b.mp4", source)

        keys = [stored.key for stored in storage.list_objects("camera-01/")]

        assert keys == ["camera-01/2026-08-10/a.mp4"]

    def test_없는_저장소를_훑어도_비어_있다(self, tmp_path: Path) -> None:
        assert list(self._build(tmp_path).list_objects()) == []

    def test_객체를_지운다(self, tmp_path: Path) -> None:
        storage = self._build(tmp_path)
        source = tmp_path / "segment.mp4"
        source.write_bytes(b"x")
        storage.put_object("camera-01/2026-08-10/a.mp4", source)

        storage.remove_object("camera-01/2026-08-10/a.mp4")

        assert list(storage.list_objects()) == []

    def test_지운_뒤_빈_디렉터리를_정리한다(self, tmp_path: Path) -> None:
        storage = self._build(tmp_path)
        source = tmp_path / "segment.mp4"
        source.write_bytes(b"x")
        storage.put_object("camera-01/2026-08-10/a.mp4", source)

        storage.remove_object("camera-01/2026-08-10/a.mp4")

        assert not (storage.root_dir / "camera-01" / "2026-08-10").exists()

    def test_없는_객체를_지워도_실패하지_않는다(self, tmp_path: Path) -> None:
        self._build(tmp_path).remove_object("camera-01/없는파일.mp4")

    def test_저장소_밖으로_나가는_키를_거부한다(self, tmp_path: Path) -> None:
        storage = self._build(tmp_path)
        source = tmp_path / "segment.mp4"
        source.write_bytes(b"x")

        with pytest.raises(ObjectStorageError, match="루트를 벗어나는"):
            storage.put_object("../../탈출.mp4", source)
