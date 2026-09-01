"""세그먼트 분할과 해상도 처리 검증."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from shared.types import Frame
from ..video_recorder import VideoRecorder
from .conftest import make_frame


class FakeWriter:
    def __init__(self, path: Path, fps: int, frame_size: tuple[int, int]) -> None:
        self.path = path
        self.fps = fps
        self.frame_size = frame_size
        self.written = 0
        self.released = False

    def write(self, image: Frame) -> None:
        self.written += 1

    def release(self) -> None:
        self.released = True


class FakeWriterFactory:
    def __init__(self) -> None:
        self.writers: list[FakeWriter] = []

    def __call__(
        self, path: Path, fps: int, frame_size: tuple[int, int]
    ) -> FakeWriter:
        writer = FakeWriter(path, fps, frame_size)
        self.writers.append(writer)
        return writer


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.current = start

    def __call__(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


def build_recorder(
    output_dir: Path, clock: FakeClock, factory: FakeWriterFactory, *, segment: int = 60
) -> VideoRecorder:
    return VideoRecorder(
        camera_id="camera-01",
        output_dir=output_dir,
        fps=20,
        segment_seconds=segment,
        writer_factory=factory,
        now=clock,
    )


def test_프레임_크기를_설정이_아니라_실제_프레임에서_가져온다(output_dir: Path) -> None:
    clock = FakeClock(datetime(2026, 8, 10, 9, 0, 0))
    factory = FakeWriterFactory()
    recorder = build_recorder(output_dir, clock, factory)

    recorder.write(make_frame(width=1280, height=720))

    assert factory.writers[0].frame_size == (1280, 720)


def test_카메라별_날짜별_디렉터리에_저장한다(output_dir: Path) -> None:
    clock = FakeClock(datetime(2026, 8, 10, 9, 0, 0))
    factory = FakeWriterFactory()
    recorder = build_recorder(output_dir, clock, factory)

    recorder.write(make_frame())

    path = factory.writers[0].path
    assert path.parent == output_dir / "camera-01" / "2026-08-10"
    assert path.name == "20260810_090000.mp4"
    assert path.parent.exists()


def test_세그먼트_시간이_지나면_새_파일로_넘어간다(output_dir: Path) -> None:
    clock = FakeClock(datetime(2026, 8, 10, 9, 0, 0))
    factory = FakeWriterFactory()
    recorder = build_recorder(output_dir, clock, factory, segment=60)

    recorder.write(make_frame())
    clock.advance(59)
    recorder.write(make_frame())
    assert len(factory.writers) == 1

    clock.advance(2)
    recorder.write(make_frame())

    assert len(factory.writers) == 2
    assert factory.writers[0].released, "이전 세그먼트를 닫지 않으면 파일이 깨진다"


def test_하루가_지나도_세그먼트_경과_시간을_옳게_잰다(output_dir: Path) -> None:
    """timedelta.seconds는 일 단위를 버린다. total_seconds를 쓰는지 확인한다."""
    clock = FakeClock(datetime(2026, 8, 10, 9, 0, 0))
    factory = FakeWriterFactory()
    recorder = build_recorder(output_dir, clock, factory, segment=86400)

    recorder.write(make_frame())
    # 25시간 경과. .seconds였다면 3600으로 읽혀 분할되지 않는다.
    clock.advance(25 * 3600)
    recorder.write(make_frame())

    assert len(factory.writers) == 2


def test_해상도가_바뀌면_세그먼트를_새로_연다(output_dir: Path) -> None:
    clock = FakeClock(datetime(2026, 8, 10, 9, 0, 0))
    factory = FakeWriterFactory()
    recorder = build_recorder(output_dir, clock, factory)

    recorder.write(make_frame(width=640, height=480))
    clock.advance(1)
    recorder.write(make_frame(width=1280, height=720))

    assert len(factory.writers) == 2
    assert factory.writers[1].frame_size == (1280, 720)


def test_close하면_writer를_해제한다(output_dir: Path) -> None:
    clock = FakeClock(datetime(2026, 8, 10, 9, 0, 0))
    factory = FakeWriterFactory()
    recorder = build_recorder(output_dir, clock, factory)
    recorder.write(make_frame())

    recorder.close()

    assert factory.writers[0].released


def test_close를_두_번_불러도_안전하다(output_dir: Path) -> None:
    clock = FakeClock(datetime(2026, 8, 10, 9, 0, 0))
    recorder = build_recorder(output_dir, clock, FakeWriterFactory())

    recorder.close()
    recorder.close()
