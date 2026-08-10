"""FFmpeg 명령 구성과 프로세스 수명 관리 검증. 실제로 FFmpeg을 띄우지 않는다."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from ..errors import RtspPublishError
from ..rtsp_publisher import RtspPublisher
from .conftest import RecordingSleep


class FakeProcess:
    def __init__(self, exit_codes: list[int | None] | None = None) -> None:
        self._exit_codes = exit_codes or [None]
        self._poll_index = 0
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        index = min(self._poll_index, len(self._exit_codes) - 1)
        self._poll_index += 1
        return self._exit_codes[index]

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        return 0


class FakeRunner:
    def __init__(self, processes: list[FakeProcess]) -> None:
        self._processes = processes
        self.commands: list[list[str]] = []

    def __call__(self, command: Sequence[str]) -> FakeProcess:
        index = min(len(self.commands), len(self._processes) - 1)
        self.commands.append(list(command))
        return self._processes[index]


class FailingRunner:
    def __call__(self, command: Sequence[str]) -> FakeProcess:
        raise OSError("ffmpeg not found")


def build_publisher(
    runner: object,
    sleep_spy: RecordingSleep,
    *,
    input_format: str = "dshow",
    device_name: str = "USB2.0 HD UVC WebCam",
) -> RtspPublisher:
    return RtspPublisher(
        device_name=device_name,
        target_url="rtsp://localhost:8554/camera",
        input_format=input_format,
        framerate=20,
        startup_wait_seconds=0.1,
        process_runner=runner,  # type: ignore[arg-type]
        sleep=sleep_spy,
    )


def test_dshow는_video_접두사를_붙인다(sleep_spy: RecordingSleep) -> None:
    publisher = build_publisher(FakeRunner([FakeProcess()]), sleep_spy)

    command = publisher.build_command()

    assert "video=USB2.0 HD UVC WebCam" in command
    assert command[command.index("-f") + 1] == "dshow"


def test_v4l2는_장치_경로를_그대로_쓴다(sleep_spy: RecordingSleep) -> None:
    publisher = build_publisher(
        FakeRunner([FakeProcess()]),
        sleep_spy,
        input_format="v4l2",
        device_name="/dev/video0",
    )

    command = publisher.build_command()

    assert "/dev/video0" in command
    assert "video=/dev/video0" not in command


def test_GOP는_프레임률의_두_배다(sleep_spy: RecordingSleep) -> None:
    publisher = build_publisher(FakeRunner([FakeProcess()]), sleep_spy)

    command = publisher.build_command()

    assert command[command.index("-g") + 1] == "40"


def test_RTSP는_TCP로_보낸다(sleep_spy: RecordingSleep) -> None:
    publisher = build_publisher(FakeRunner([FakeProcess()]), sleep_spy)

    command = publisher.build_command()

    assert command[command.index("-rtsp_transport") + 1] == "tcp"


def test_시작하면_경로가_생기기를_기다린다(sleep_spy: RecordingSleep) -> None:
    publisher = build_publisher(FakeRunner([FakeProcess()]), sleep_spy)

    publisher.start()

    assert sleep_spy.calls == [0.1]
    assert publisher.is_running()


def test_FFmpeg이_없으면_예외를_올린다(sleep_spy: RecordingSleep) -> None:
    publisher = build_publisher(FailingRunner(), sleep_spy)

    with pytest.raises(RtspPublishError, match="실행하지 못했습니다"):
        publisher.start()


def test_시작_직후_죽으면_예외를_올린다(sleep_spy: RecordingSleep) -> None:
    publisher = build_publisher(FakeRunner([FakeProcess(exit_codes=[1])]), sleep_spy)

    with pytest.raises(RtspPublishError, match="시작 직후 종료"):
        publisher.start()

    assert not publisher.is_running()


def test_두_번_시작해도_프로세스는_하나다(sleep_spy: RecordingSleep) -> None:
    runner = FakeRunner([FakeProcess()])
    publisher = build_publisher(runner, sleep_spy)

    publisher.start()
    publisher.start()

    assert len(runner.commands) == 1


def test_stop은_프로세스를_종료한다(sleep_spy: RecordingSleep) -> None:
    process = FakeProcess()
    publisher = build_publisher(FakeRunner([process]), sleep_spy)
    publisher.start()

    publisher.stop()

    assert process.terminated
    assert not publisher.is_running()


def test_시작하지_않았으면_stop은_아무것도_하지_않는다(
    sleep_spy: RecordingSleep,
) -> None:
    publisher = build_publisher(FakeRunner([FakeProcess()]), sleep_spy)

    publisher.stop()  # 예외가 나지 않아야 한다


def test_로그용_URL에_자격_증명이_없다(sleep_spy: RecordingSleep) -> None:
    publisher = RtspPublisher(
        device_name="cam",
        target_url="rtsp://admin:secret@10.0.0.5:8554/camera",
        input_format="dshow",
        framerate=20,
        startup_wait_seconds=0,
        process_runner=FakeRunner([FakeProcess()]),  # type: ignore[arg-type]
        sleep=sleep_spy,
    )

    assert publisher.masked_target_url == "rtsp://***@10.0.0.5:8554/camera"
