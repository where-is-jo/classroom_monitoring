from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from auto_labeling.capture_webcam import capture_video
from auto_labeling.errors import AutoLabelingError


class FakeCapture:
    def __init__(self, frame: np.ndarray) -> None:
        self.frame = frame
        self.released = False
        self.settings: list[tuple[int, float]] = []

    def set(self, setting: int, value: float) -> bool:
        self.settings.append((setting, value))
        return True

    def isOpened(self) -> bool:
        return True

    def read(self) -> tuple[bool, np.ndarray]:
        return True, self.frame.copy()

    def release(self) -> None:
        self.released = True


class FakeWriter:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.touch()
        self.frames: list[np.ndarray] = []
        self.released = False

    def isOpened(self) -> bool:
        return True

    def write(self, frame: np.ndarray) -> None:
        self.frames.append(frame.copy())

    def release(self) -> None:
        self.released = True


class FakeCv:
    CAP_DSHOW = 700
    CAP_PROP_FRAME_WIDTH = 3
    CAP_PROP_FRAME_HEIGHT = 4
    CAP_PROP_FPS = 5
    WINDOW_NORMAL = 0
    FONT_HERSHEY_SIMPLEX = 0
    LINE_AA = 0

    def __init__(
        self,
        keys: list[int | BaseException],
        *,
        frame_size: tuple[int, int] = (640, 480),
    ) -> None:
        width, height = frame_size
        self.capture = FakeCapture(np.zeros((height, width, 3), dtype=np.uint8))
        self.keys: Iterator[int | BaseException] = iter(keys)
        self.writer: FakeWriter | None = None
        self.video_capture_arguments: tuple[object, ...] | None = None
        self.writer_size: tuple[int, int] | None = None
        self.windows_destroyed = False

    def VideoCapture(self, *args: object) -> FakeCapture:
        self.video_capture_arguments = args
        return self.capture

    def VideoWriter(
        self,
        path: str,
        fourcc: int,
        fps: float,
        frame_size: tuple[int, int],
    ) -> FakeWriter:
        del fourcc, fps
        self.writer_size = frame_size
        self.writer = FakeWriter(path)
        return self.writer

    def VideoWriter_fourcc(self, *codec: str) -> int:
        assert codec == ("m", "p", "4", "v")
        return 1

    def namedWindow(self, name: str, mode: int) -> None:
        del name, mode

    def rectangle(self, *args: object) -> None:
        del args

    def putText(self, *args: object) -> None:
        del args

    def imshow(self, name: str, frame: np.ndarray) -> None:
        del name, frame

    def waitKey(self, delay: int) -> int:
        del delay
        value = next(self.keys)
        if isinstance(value, BaseException):
            raise value
        return value

    def destroyAllWindows(self) -> None:
        self.windows_destroyed = True


class StepClock:
    def __init__(self) -> None:
        self.value = -1.0

    def __call__(self) -> float:
        self.value += 1.0
        return self.value


def test_escape_during_recording_finishes_mp4(tmp_path: Path) -> None:
    output = tmp_path / "capture.mp4"
    fake_cv = FakeCv([32, 27], frame_size=(800, 600))

    result = capture_video(
        output=output,
        countdown=0,
        cv_module=fake_cv,
        clock=StepClock(),
    )

    assert result is not None
    assert result.frame_count == 1
    assert result.output == str(output.resolve())
    assert output.exists()
    assert fake_cv.writer_size == (800, 600)
    assert fake_cv.writer is not None and fake_cv.writer.released
    assert fake_cv.capture.released
    assert fake_cv.windows_destroyed


def test_control_c_during_recording_finishes_mp4(tmp_path: Path) -> None:
    output = tmp_path / "capture.mp4"
    fake_cv = FakeCv([32, KeyboardInterrupt()])

    result = capture_video(
        output=output,
        countdown=0,
        cv_module=fake_cv,
        clock=StepClock(),
    )

    assert result is not None
    assert result.frame_count == 1
    assert output.exists()


def test_escape_before_recording_cancels_without_file(tmp_path: Path) -> None:
    output = tmp_path / "capture.mp4"
    fake_cv = FakeCv([27])

    result = capture_video(output=output, cv_module=fake_cv)

    assert result is None
    assert not output.exists()
    assert fake_cv.capture.released


def test_rtsp_source_does_not_change_camera_properties(tmp_path: Path) -> None:
    output = tmp_path / "capture.mp4"
    fake_cv = FakeCv([27])

    capture_video(
        output=output,
        rtsp_url="rtsp://127.0.0.1:8554/camera-01",
        cv_module=fake_cv,
    )

    assert fake_cv.video_capture_arguments == ("rtsp://127.0.0.1:8554/camera-01",)
    assert fake_cv.capture.settings == []


def test_existing_output_is_not_overwritten(tmp_path: Path) -> None:
    output = tmp_path / "capture.mp4"
    output.write_bytes(b"existing")

    with pytest.raises(AutoLabelingError, match="덮어쓸 수 없습니다"):
        capture_video(output=output, cv_module=FakeCv([27]))

    assert output.read_bytes() == b"existing"


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"camera_index": -1}, "camera index"),
        ({"rtsp_url": "http://camera"}, "RTSP URL"),
        ({"fps": 0}, "FPS"),
        ({"countdown": -1}, "카운트다운"),
    ],
)
def test_invalid_capture_options_are_rejected(
    tmp_path: Path,
    options: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(AutoLabelingError, match=message):
        capture_video(
            output=tmp_path / "capture.mp4",
            cv_module=FakeCv([27]),
            **options,
        )
