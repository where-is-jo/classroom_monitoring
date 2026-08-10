"""프레임 샘플링 로직을 고정 입력으로 검증한다."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from ..camera_reader import Frame
from ..frame_capture import FrameCapture, should_sample
from .conftest import make_frame


class SpyImageWriter:
    def __init__(self, *, succeeds: bool = True) -> None:
        self.succeeds = succeeds
        self.paths: list[Path] = []

    def __call__(self, path: Path, frame: Frame) -> bool:
        self.paths.append(path)
        return self.succeeds


class TestShouldSample:
    @pytest.mark.parametrize(
        ("frame_index", "expected"),
        [(0, True), (1, False), (19, False), (20, True), (40, True), (41, False)],
    )
    def test_주기마다_한_장을_고른다(self, frame_index: int, expected: bool) -> None:
        assert should_sample(frame_index, 20) is expected

    def test_주기가_1이면_모두_고른다(self) -> None:
        assert all(should_sample(index, 1) for index in range(5))

    def test_주기가_0이면_거부한다(self) -> None:
        with pytest.raises(ValueError, match="1 이상"):
            should_sample(0, 0)


class TestFrameCapture:
    def _build(
        self, output_dir: Path, writer: SpyImageWriter, *, interval: int = 3
    ) -> FrameCapture:
        return FrameCapture(
            camera_id="camera-01",
            output_dir=output_dir,
            interval_frames=interval,
            image_writer=writer,
            now=lambda: datetime(2026, 8, 10, 9, 0, 0, 123456),
        )

    def test_주기에_해당하는_프레임만_저장한다(self, output_dir: Path) -> None:
        writer = SpyImageWriter()
        capture = self._build(output_dir, writer, interval=3)

        results = [capture.offer(make_frame()) for _ in range(7)]

        assert [result is not None for result in results] == [
            True, False, False, True, False, False, True
        ]
        assert len(writer.paths) == 3

    def test_카메라별_날짜별_디렉터리에_저장한다(self, output_dir: Path) -> None:
        writer = SpyImageWriter()
        capture = self._build(output_dir, writer, interval=1)

        path = capture.offer(make_frame())

        assert path is not None
        assert path.parent == output_dir / "camera-01" / "2026-08-10"
        assert path.name == "20260810_090000_123456.jpg"
        assert path.parent.exists()

    def test_저장에_실패하면_None을_돌려준다(self, output_dir: Path) -> None:
        writer = SpyImageWriter(succeeds=False)
        capture = self._build(output_dir, writer, interval=1)

        assert capture.offer(make_frame()) is None

    def test_저장하지_않는_프레임에는_디렉터리를_만들지_않는다(
        self, output_dir: Path
    ) -> None:
        writer = SpyImageWriter()
        capture = self._build(output_dir, writer, interval=100)

        capture.offer(make_frame())  # 0번 → 저장
        writer.paths.clear()
        capture.offer(make_frame())  # 1번 → 건너뜀

        assert writer.paths == []
