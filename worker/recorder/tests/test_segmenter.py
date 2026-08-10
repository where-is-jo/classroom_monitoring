"""FFmpeg 명령 구성과 프로세스 수명 검증. 실제로 FFmpeg을 띄우지 않는다."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from shared.camera_sources import CameraSource

from ..errors import SegmentationError
from ..segmenter import Segmenter, parse_segment_recorded_at
from .conftest import FakeProcess, FakeRunner

SOURCE = CameraSource(camera_id="camera-01", rtsp_url="rtsp://localhost:8554/camera")


def build_segmenter(
    runner: object, output_dir: Path, *, segment_seconds: int = 600
) -> Segmenter:
    return Segmenter(
        SOURCE,
        output_dir=output_dir,
        segment_seconds=segment_seconds,
        process_runner=runner,  # type: ignore[arg-type]
        sleep=lambda seconds: None,
        startup_wait_seconds=0,
    )


class TestBuildCommand:
    def test_재인코딩하지_않는다(self, tmp_path: Path) -> None:
        """디코딩하면 CPU를 써서 추론이 느려지고 원본이 열화된다."""
        command = build_segmenter(FakeRunner(), tmp_path).build_command()

        assert command[command.index("-c") + 1] == "copy"

    def test_RTSP를_TCP로_받는다(self, tmp_path: Path) -> None:
        command = build_segmenter(FakeRunner(), tmp_path).build_command()

        assert command[command.index("-rtsp_transport") + 1] == "tcp"

    def test_세그먼트_길이를_설정에서_가져온다(self, tmp_path: Path) -> None:
        command = build_segmenter(FakeRunner(), tmp_path, segment_seconds=300).build_command()

        assert command[command.index("-f") + 1] == "segment"
        assert command[command.index("-segment_time") + 1] == "300"

    def test_출력_경로에_UTC_시각_패턴을_쓴다(self, tmp_path: Path) -> None:
        command = build_segmenter(FakeRunner(), tmp_path).build_command()

        assert command[-1] == str(tmp_path / "%Y%m%dT%H%M%SZ.mp4")
        assert command[command.index("-strftime") + 1] == "1"


class TestLifecycle:
    def test_시작하면_출력_디렉터리를_만든다(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "camera-01"
        segmenter = build_segmenter(FakeRunner(), output_dir)

        segmenter.start()

        assert output_dir.exists()
        assert segmenter.is_running()

    def test_FFmpeg이_없으면_예외를_올린다(self, tmp_path: Path) -> None:
        class FailingRunner:
            def __call__(self, command: object) -> FakeProcess:
                raise OSError("ffmpeg not found")

        segmenter = build_segmenter(FailingRunner(), tmp_path)

        with pytest.raises(SegmentationError, match="실행하지 못했습니다"):
            segmenter.start()

    def test_시작_직후_죽으면_예외를_올린다(self, tmp_path: Path) -> None:
        segmenter = build_segmenter(FakeRunner([FakeProcess(exit_codes=[1])]), tmp_path)

        with pytest.raises(SegmentationError, match="시작 직후 종료"):
            segmenter.start()

        assert not segmenter.is_running()

    def test_두_번_시작해도_프로세스는_하나다(self, tmp_path: Path) -> None:
        runner = FakeRunner()
        segmenter = build_segmenter(runner, tmp_path)

        segmenter.start()
        segmenter.start()

        assert len(runner.commands) == 1

    def test_stop은_kill이_아니라_terminate로_끝낸다(self, tmp_path: Path) -> None:
        """kill로 끊으면 마지막 세그먼트의 moov atom이 안 써져 재생 불가가 된다."""
        process = FakeProcess()
        segmenter = build_segmenter(FakeRunner([process]), tmp_path)
        segmenter.start()

        segmenter.stop()

        assert process.terminated
        assert not process.killed
        assert not segmenter.is_running()

    def test_시작하지_않았으면_stop은_아무것도_하지_않는다(self, tmp_path: Path) -> None:
        build_segmenter(FakeRunner(), tmp_path).stop()

    def test_오류_메시지에_자격_증명이_들어가지_않는다(self, tmp_path: Path) -> None:
        source = CameraSource(
            camera_id="camera-01", rtsp_url="rtsp://admin:SuperSecret@10.0.0.5:8554/cam"
        )
        segmenter = Segmenter(
            source,
            output_dir=tmp_path,
            segment_seconds=600,
            process_runner=FakeRunner([FakeProcess(exit_codes=[1])]),  # type: ignore[arg-type]
            sleep=lambda seconds: None,
            startup_wait_seconds=0,
        )

        with pytest.raises(SegmentationError) as error:
            segmenter.start()

        assert "SuperSecret" not in str(error.value)


class TestParseRecordedAt:
    def test_파일_이름에서_시각을_읽는다(self) -> None:
        recorded_at = parse_segment_recorded_at(Path("20260810T090530Z.mp4"))

        assert recorded_at == datetime(2026, 8, 10, 9, 5, 30, tzinfo=UTC)

    @pytest.mark.parametrize(
        "name", ["recording.mp4", "20260810.mp4", "메모.txt", "20260810T99Z.mp4"]
    )
    def test_형식이_다르면_None이다(self, name: str) -> None:
        assert parse_segment_recorded_at(Path(name)) is None
