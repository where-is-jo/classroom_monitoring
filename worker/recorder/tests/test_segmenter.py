"""FFmpeg 명령 구성과 프로세스 수명 검증. 실제로 FFmpeg을 띄우지 않는다."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from shared.camera_sources import CameraSource

from ..errors import SegmentationError
from ..object_keys import build_object_key
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

        assert command[-1] == str(tmp_path / "%Y%m%d_%H%M%S.mp4")
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

    def test_stop은_stdin으로_q를_보내_스스로_끝내게_한다(self, tmp_path: Path) -> None:
        """Windows에서 terminate는 TerminateProcess라 moov atom을 못 쓴다.

        그러면 마지막 세그먼트가 48바이트짜리 재생 불가 파일로 남는다.
        실제 FFmpeg으로 확인한 차이다.
        """
        process = FakeProcess()
        segmenter = build_segmenter(FakeRunner([process]), tmp_path)
        segmenter.start()

        segmenter.stop()

        assert process.stdin.written == b"q"
        assert process.stdin.closed
        assert not process.terminated, "정상 종료했으면 강제 종료하지 않는다"
        assert not process.killed
        assert not segmenter.is_running()

    def test_q에_응답하지_않으면_terminate로_넘어간다(self, tmp_path: Path) -> None:
        process = FakeProcess(ignores_quit=True)
        segmenter = build_segmenter(FakeRunner([process]), tmp_path)
        segmenter.start()

        segmenter.stop()

        assert process.terminated
        assert not process.killed, "terminate로 끝났으면 kill까지 가지 않는다"

    def test_terminate에도_응답하지_않으면_kill한다(self, tmp_path: Path) -> None:
        """끝나지 않는 FFmpeg을 남기면 다음 실행이 장치를 잡지 못한다."""
        process = FakeProcess(ignores_quit=True, ignores_terminate=True)
        segmenter = build_segmenter(FakeRunner([process]), tmp_path)
        segmenter.start()

        segmenter.stop()

        assert process.killed

    def test_stdin이_이미_닫혔으면_강제_종료로_넘어간다(self, tmp_path: Path) -> None:
        """프로세스가 먼저 죽었을 때 파이프 쓰기가 실패한다."""
        process = FakeProcess(broken_stdin=True)
        segmenter = build_segmenter(FakeRunner([process]), tmp_path)
        segmenter.start()

        segmenter.stop()

        assert process.terminated

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
    def test_파일_이름을_로컬_시각으로_읽는다(self) -> None:
        """FFmpeg의 -strftime은 localtime을 쓴다. 실제 FFmpeg으로 확인한 사실이다."""
        recorded_at = parse_segment_recorded_at(Path("20260810_090530.mp4"))

        assert recorded_at == datetime(2026, 8, 10, 9, 5, 30).astimezone()

    def test_읽은_시각에는_시각대가_붙는다(self) -> None:
        """naive로 두면 보존 기간·객체 키 계산에서 UTC와 섞인다."""
        recorded_at = parse_segment_recorded_at(Path("20260810_090530.mp4"))

        assert recorded_at is not None
        assert recorded_at.tzinfo is not None

    def test_객체_키는_UTC로_바뀐다(self) -> None:
        """이름은 로컬, 키는 UTC. 이 변환이 빠지면 키의 시각이 시각대만큼 어긋난다."""
        recorded_at = parse_segment_recorded_at(Path("20260810_090530.mp4"))

        assert recorded_at is not None
        expected = datetime(2026, 8, 10, 9, 5, 30).astimezone().astimezone(UTC)
        assert build_object_key("camera-01", recorded_at) == (
            f"camera-01/{expected:%Y-%m-%d}/{expected:%Y%m%dT%H%M%SZ}.mp4"
        )

    @pytest.mark.parametrize(
        "name",
        ["recording.mp4", "20260810.mp4", "메모.txt", "20260810_99.mp4",
         "20260810T090530Z.mp4"],
    )
    def test_형식이_다르면_None이다(self, name: str) -> None:
        assert parse_segment_recorded_at(Path(name)) is None
