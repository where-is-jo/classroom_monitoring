"""자동 라벨링 입력 영상을 사용자가 종료할 때까지 녹화한다.

USB 웹캠을 직접 열거나, 이미 송출 중인 RTSP 주소를 입력으로 받을 수 있다.
녹화가 시작된 뒤 Esc 또는 Ctrl+C를 누르면 현재 MP4를 정상적으로 닫아 보존한다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2

from .errors import AutoLabelingError

WINDOW_NAME = "YOLO auto-labeling capture - SPACE start / ESC stop and save"


@dataclass(frozen=True)
class CaptureResult:
    output: str
    frame_count: int
    fps: float
    duration_seconds: float


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m auto_labeling.capture_webcam",
        description=(
            "자동 라벨링 입력 영상을 녹화합니다. Space로 시작하고 녹화 중 "
            "Esc 또는 Ctrl+C로 저장 후 종료합니다."
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument("--camera-index", type=int)
    source_group.add_argument(
        "--rtsp-url",
        help="CCTV 송출과 동시에 녹화할 때 사용할 RTSP 입력 URL",
    )
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--countdown", type=float, default=5.0)
    return parser


def capture_video(
    *,
    output: Path,
    camera_index: int = 0,
    rtsp_url: str | None = None,
    width: int = 640,
    height: int = 480,
    fps: float = 30.0,
    countdown: float = 5.0,
    cv_module: Any = cv2,
    clock: Callable[[], float] = time.monotonic,
) -> CaptureResult | None:
    """영상을 녹화한다. 녹화 전 취소하면 None을 반환한다."""
    _validate_capture_options(
        camera_index=camera_index,
        rtsp_url=rtsp_url,
        width=width,
        height=height,
        fps=fps,
        countdown=countdown,
    )

    output = output.resolve()
    if output.exists():
        raise AutoLabelingError(f"기존 출력 파일을 덮어쓸 수 없습니다: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    partial = output.with_name(f".{output.stem}.partial{output.suffix}")
    if partial.exists():
        raise AutoLabelingError(f"미완료 촬영 파일이 이미 있습니다: {partial}")

    if rtsp_url is not None:
        capture = cv_module.VideoCapture(rtsp_url)
    elif sys.platform.startswith("win"):
        capture = cv_module.VideoCapture(camera_index, cv_module.CAP_DSHOW)
    else:
        capture = cv_module.VideoCapture(camera_index)

    if rtsp_url is None:
        capture.set(cv_module.CAP_PROP_FRAME_WIDTH, width)
        capture.set(cv_module.CAP_PROP_FRAME_HEIGHT, height)
        capture.set(cv_module.CAP_PROP_FPS, fps)

    if not capture.isOpened():
        capture.release()
        source = rtsp_url if rtsp_url is not None else f"camera index {camera_index}"
        raise AutoLabelingError(f"영상 입력을 열 수 없습니다: {source}")

    writer: Any | None = None
    countdown_started: float | None = None
    recording_started: float | None = None
    frame_count = 0
    completed = False
    stopped_at = 0.0

    try:
        cv_module.namedWindow(WINDOW_NAME, cv_module.WINDOW_NORMAL)
        try:
            while True:
                ok, frame = capture.read()
                if not ok or frame is None:
                    raise AutoLabelingError("영상 프레임을 읽지 못했습니다.")

                now = clock()
                display = frame.copy()
                if recording_started is not None:
                    assert writer is not None
                    writer.write(frame)
                    frame_count += 1
                    elapsed = max(now - recording_started, 0.0)
                    _draw_status(
                        display,
                        f"REC {elapsed:07.1f}s - ESC stop and save",
                        (0, 0, 255),
                        cv_module,
                    )
                elif countdown_started is not None:
                    remaining = countdown - (now - countdown_started)
                    if remaining <= 0:
                        frame_height, frame_width = frame.shape[:2]
                        writer = cv_module.VideoWriter(
                            str(partial),
                            cv_module.VideoWriter_fourcc(*"mp4v"),
                            fps,
                            (frame_width, frame_height),
                        )
                        if not writer.isOpened():
                            raise AutoLabelingError("MP4 출력 파일을 열 수 없습니다.")
                        recording_started = now
                        continue
                    _draw_status(
                        display,
                        f"Recording starts in {max(1, int(remaining + 0.999))}",
                        (0, 255, 255),
                        cv_module,
                    )
                else:
                    _draw_status(
                        display,
                        "SPACE start / ESC cancel",
                        (0, 255, 0),
                        cv_module,
                    )

                cv_module.imshow(WINDOW_NAME, display)
                key = cv_module.waitKey(1) & 0xFF
                if key == 27:
                    if recording_started is None:
                        return None
                    completed = frame_count > 0
                    stopped_at = now
                    break
                if key == 32 and countdown_started is None:
                    countdown_started = now
        except KeyboardInterrupt:
            if recording_started is None or frame_count == 0:
                return None
            completed = True
            stopped_at = clock()
    finally:
        capture.release()
        if writer is not None:
            writer.release()
        cv_module.destroyAllWindows()
        if completed:
            partial.replace(output)
        elif partial.exists():
            partial.unlink()

    assert recording_started is not None
    return CaptureResult(
        output=str(output),
        frame_count=frame_count,
        fps=fps,
        duration_seconds=max(stopped_at - recording_started, 0.0),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    camera_index = args.camera_index if args.camera_index is not None else 0
    try:
        result = capture_video(
            output=args.output,
            camera_index=camera_index,
            rtsp_url=args.rtsp_url,
            width=args.width,
            height=args.height,
            fps=args.fps,
            countdown=args.countdown,
        )
    except AutoLabelingError as error:
        print(f"오류: {error}", file=sys.stderr)
        return 2
    except OSError:
        print("오류: 촬영 파일 작업을 완료할 수 없습니다.", file=sys.stderr)
        return 2

    if result is None:
        print("촬영을 시작하기 전에 취소했습니다.")
        return 130

    print(json.dumps({"status": "captured", **asdict(result)}, ensure_ascii=False))
    return 0


def _validate_capture_options(
    *,
    camera_index: int,
    rtsp_url: str | None,
    width: int,
    height: int,
    fps: float,
    countdown: float,
) -> None:
    if camera_index < 0:
        raise AutoLabelingError("camera index는 0 이상이어야 합니다.")
    if rtsp_url is not None and not rtsp_url.startswith("rtsp://"):
        raise AutoLabelingError("RTSP URL은 rtsp:// 로 시작해야 합니다.")
    if width <= 0 or height <= 0:
        raise AutoLabelingError("촬영 해상도는 0보다 커야 합니다.")
    if fps <= 0:
        raise AutoLabelingError("촬영 FPS는 0보다 커야 합니다.")
    if countdown < 0:
        raise AutoLabelingError("카운트다운은 0 이상이어야 합니다.")


def _draw_status(
    frame: Any,
    text: str,
    color: tuple[int, int, int],
    cv_module: Any,
) -> None:
    frame_width = int(frame.shape[1])
    cv_module.rectangle(frame, (0, 0), (frame_width, 52), (0, 0, 0), -1)
    cv_module.putText(
        frame,
        text,
        (12, 35),
        cv_module.FONT_HERSHEY_SIMPLEX,
        0.75,
        color,
        2,
        cv_module.LINE_AA,
    )


if __name__ == "__main__":
    raise SystemExit(main())
