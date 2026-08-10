"""연결 → 끊김 → 재시도 → 복구 상태 전이 검증. 실제 카메라를 쓰지 않는다."""

from __future__ import annotations

import pytest

from ..camera_reader import CameraReader, ConnectionState
from ..config import CameraSource
from ..errors import CameraConnectionError
from .conftest import FakeCapture, FakeCaptureFactory, RecordingSleep

SOURCE = CameraSource(camera_id="camera-01", rtsp_url="rtsp://localhost:8554/camera")


def build_reader(
    factory: FakeCaptureFactory,
    sleep_spy: RecordingSleep,
    *,
    max_retry: int = 3,
    read_failure_tolerance: int = 2,
) -> CameraReader:
    return CameraReader(
        SOURCE,
        max_retry=max_retry,
        reconnect_delay_seconds=0.5,
        read_failure_tolerance=read_failure_tolerance,
        capture_factory=factory,
        sleep=sleep_spy,
    )


def test_연결에_성공하면_상태가_connected가_된다(sleep_spy: RecordingSleep) -> None:
    factory = FakeCaptureFactory([FakeCapture(is_open=True)])
    reader = build_reader(factory, sleep_spy)

    reader.connect()

    assert reader.state is ConnectionState.CONNECTED
    assert factory.call_count == 1
    assert sleep_spy.calls == []


def test_열리지_않은_capture는_해제하고_재시도한다(sleep_spy: RecordingSleep) -> None:
    failed = FakeCapture(is_open=False)
    opened = FakeCapture(is_open=True)
    factory = FakeCaptureFactory([failed, opened])
    reader = build_reader(factory, sleep_spy)

    reader.connect()

    assert reader.state is ConnectionState.CONNECTED
    assert failed.released, "실패한 capture를 해제하지 않으면 핸들이 쌓인다"
    assert sleep_spy.calls == [0.5]


def test_최대_시도를_모두_실패하면_예외와_함께_failed가_된다(
    sleep_spy: RecordingSleep,
) -> None:
    factory = FakeCaptureFactory([FakeCapture(is_open=False)])
    reader = build_reader(factory, sleep_spy, max_retry=3)

    with pytest.raises(CameraConnectionError) as error:
        reader.connect()

    assert reader.state is ConnectionState.FAILED
    assert factory.call_count == 3
    # 마지막 시도 뒤에는 기다리지 않는다.
    assert sleep_spy.calls == [0.5, 0.5]
    assert "camera-01" in str(error.value)


def test_실패_메시지에_자격_증명이_들어가지_않는다(sleep_spy: RecordingSleep) -> None:
    source = CameraSource(
        camera_id="camera-01", rtsp_url="rtsp://admin:secret@10.0.0.5:8554/camera"
    )
    reader = CameraReader(
        source,
        max_retry=1,
        reconnect_delay_seconds=0,
        read_failure_tolerance=1,
        capture_factory=FakeCaptureFactory([FakeCapture(is_open=False)]),
        sleep=sleep_spy,
    )

    with pytest.raises(CameraConnectionError) as error:
        reader.connect()

    message = str(error.value)
    assert "secret" not in message
    assert "admin" not in message
    assert "10.0.0.5:8554" in message


def test_연결_전에_읽으면_예외를_올린다(sleep_spy: RecordingSleep) -> None:
    reader = build_reader(FakeCaptureFactory([FakeCapture()]), sleep_spy)

    with pytest.raises(CameraConnectionError):
        reader.read()


def test_일시적인_읽기_실패는_None으로_알리고_연결을_유지한다(
    sleep_spy: RecordingSleep,
) -> None:
    capture = FakeCapture(is_open=True, read_results=[False, True])
    factory = FakeCaptureFactory([capture])
    reader = build_reader(factory, sleep_spy, read_failure_tolerance=3)
    reader.connect()

    assert reader.read() is None
    assert reader.state is ConnectionState.CONNECTED
    assert factory.call_count == 1, "허용치 안에서는 재연결하지 않는다"
    assert reader.read() is not None


def test_연속_실패가_허용치를_넘으면_재연결한다(sleep_spy: RecordingSleep) -> None:
    broken = FakeCapture(is_open=True, read_results=[False])
    recovered = FakeCapture(is_open=True, read_results=[True])
    factory = FakeCaptureFactory([broken, recovered])
    reader = build_reader(factory, sleep_spy, read_failure_tolerance=2)
    reader.connect()

    assert reader.read() is None
    assert reader.read() is None  # 허용치 도달 → 재연결

    assert broken.released
    assert factory.call_count == 2
    assert reader.state is ConnectionState.CONNECTED
    assert reader.read() is not None


def test_재연결까지_실패하면_예외를_올린다(sleep_spy: RecordingSleep) -> None:
    broken = FakeCapture(is_open=True, read_results=[False])
    factory = FakeCaptureFactory([broken, FakeCapture(is_open=False)])
    reader = build_reader(
        factory, sleep_spy, max_retry=2, read_failure_tolerance=1
    )
    reader.connect()

    with pytest.raises(CameraConnectionError):
        reader.read()

    assert reader.state is ConnectionState.FAILED


def test_성공하면_연속_실패_횟수가_초기화된다(sleep_spy: RecordingSleep) -> None:
    capture = FakeCapture(is_open=True, read_results=[False, True, False, True])
    factory = FakeCaptureFactory([capture])
    reader = build_reader(factory, sleep_spy, read_failure_tolerance=2)
    reader.connect()

    assert reader.read() is None
    assert reader.read() is not None
    assert reader.read() is None
    assert factory.call_count == 1, "중간에 성공했으면 재연결하지 않는다"


def test_close하면_capture를_해제하고_stopped가_된다(sleep_spy: RecordingSleep) -> None:
    capture = FakeCapture(is_open=True)
    reader = build_reader(FakeCaptureFactory([capture]), sleep_spy)
    reader.connect()

    reader.close()

    assert capture.released
    assert reader.state is ConnectionState.STOPPED
