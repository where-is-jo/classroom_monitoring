"""프레임 저장기 검증.

샘플링 판단은 여기 없다. CameraPipeline이 고른 프레임만 넘어온다.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from shared.types import Frame

from ..frame_capture import FrameCapture
from .conftest import make_frame


class SpyImageWriter:
    def __init__(self, *, succeeds: bool = True) -> None:
        self.succeeds = succeeds
        self.paths: list[Path] = []

    def __call__(self, path: Path, frame: Frame) -> bool:
        self.paths.append(path)
        return self.succeeds


def build_capture(output_dir: Path, writer: SpyImageWriter) -> FrameCapture:
    return FrameCapture(
        camera_id="camera-01",
        output_dir=output_dir,
        image_writer=writer,
        now=lambda: datetime(2026, 8, 10, 9, 0, 0, 123456),
    )


def test_넘겨받은_프레임을_저장한다(output_dir: Path) -> None:
    writer = SpyImageWriter()
    capture = build_capture(output_dir, writer)

    path = capture.save(make_frame())

    assert path is not None
    assert len(writer.paths) == 1


def test_카메라별_날짜별_디렉터리에_저장한다(output_dir: Path) -> None:
    writer = SpyImageWriter()
    capture = build_capture(output_dir, writer)

    path = capture.save(make_frame())

    assert path is not None
    assert path.parent == output_dir / "camera-01" / "2026-08-10"
    assert path.name == "20260810_090000_123456.jpg"
    assert path.parent.exists()


def test_저장에_실패하면_None을_돌려준다(output_dir: Path) -> None:
    writer = SpyImageWriter(succeeds=False)
    capture = build_capture(output_dir, writer)

    assert capture.save(make_frame()) is None
