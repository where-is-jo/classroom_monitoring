"""CCTV 기준 프레임에서 신원 인계용 문 영역을 선택한다.

실제 배치 좌표를 코드나 예제 설정에 추측해 넣지 않는다. 운영자가 CCTV 기준 프레임에서
문 영역을 직접 선택하면 ``IDENTITY_HANDOVER_ROUTES``에 넣을 정규화 JSON과 확인용
preview를 만든다.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

import cv2
from inference.identity_handover import IdentityHandoverRoute

PixelRect = tuple[int, int, int, int]


def normalize_pixel_rect(
    rect: PixelRect, frame_shape: tuple[int, ...]
) -> tuple[float, float, float, float]:
    """OpenCV의 ``x, y, width, height``를 0~1 사각형으로 바꾼다."""
    if len(frame_shape) < 2:
        raise ValueError("프레임 shape에는 높이와 너비가 필요합니다.")
    frame_height, frame_width = frame_shape[:2]
    x, y, width, height = rect
    if (
        frame_width <= 0
        or frame_height <= 0
        or x < 0
        or y < 0
        or width <= 0
        or height <= 0
        or x + width > frame_width
        or y + height > frame_height
    ):
        raise ValueError("문 영역은 프레임 안의 넓이가 있는 사각형이어야 합니다.")
    return (
        x / frame_width,
        y / frame_height,
        (x + width) / frame_width,
        (y + height) / frame_height,
    )


def build_route_json(
    *,
    entry_camera_id: str,
    classroom_camera_id: str,
    zone: tuple[float, float, float, float],
) -> str:
    """검증된 route를 환경변수에 넣을 한 줄 JSON으로 만든다."""
    route = IdentityHandoverRoute(entry_camera_id, classroom_camera_id, zone)
    return json.dumps(
        [
            {
                "entry_camera_id": route.entry_camera_id,
                "classroom_camera_id": route.classroom_camera_id,
                "classroom_entry_zone": route.classroom_entry_zone,
            }
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CCTV 기준 이미지에서 신원 인계용 문 영역을 선택합니다."
    )
    parser.add_argument("image", type=Path, help="CCTV 기준 이미지 경로")
    parser.add_argument("--entry-camera-id", required=True)
    parser.add_argument("--classroom-camera-id", required=True)
    parser.add_argument(
        "--rect",
        type=int,
        nargs=4,
        metavar=("X", "Y", "WIDTH", "HEIGHT"),
        help="GUI 대신 픽셀 사각형을 직접 지정",
    )
    parser.add_argument(
        "--preview-output",
        type=Path,
        help="선택 영역을 그린 확인용 이미지 경로",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    frame = cv2.imread(str(args.image))
    if frame is None:
        raise SystemExit(f"CCTV 기준 이미지를 읽을 수 없습니다: {args.image}")

    if args.rect is None:
        window_name = "CCTV entrance zone - drag rectangle and press ENTER"
        try:
            selected = cv2.selectROI(
                window_name,
                frame,
                showCrosshair=True,
                fromCenter=False,
            )
        finally:
            cv2.destroyAllWindows()
        rect = tuple(int(value) for value in selected)
    else:
        rect = tuple(args.rect)

    try:
        zone = normalize_pixel_rect(rect, frame.shape)
    except ValueError as error:
        raise SystemExit(str(error)) from error

    route_json = build_route_json(
        entry_camera_id=args.entry_camera_id,
        classroom_camera_id=args.classroom_camera_id,
        zone=zone,
    )
    print(f"IDENTITY_HANDOVER_ROUTES={route_json}")

    if args.preview_output is not None:
        x, y, width, height = rect
        preview = frame.copy()
        cv2.rectangle(preview, (x, y), (x + width, y + height), (0, 255, 255), 4)
        args.preview_output.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(args.preview_output), preview):
            raise SystemExit(f"preview 이미지를 저장하지 못했습니다: {args.preview_output}")
        print(f"preview={args.preview_output.resolve()}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
