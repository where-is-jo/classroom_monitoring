from __future__ import annotations

import argparse
import logging
from pathlib import Path

import cv2

from .config import InferenceSettings
from .model import Yolo8nDetector
from .processor import InferenceProcessor


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
    )


def main() -> int:
    _configure_logging()
    settings = InferenceSettings()

    parser = argparse.ArgumentParser(
        description="YOLO8n으로 이미지 파일에서 사람과 수화기를 탐지합니다."
    )
    parser.add_argument("image", help="검사할 이미지 파일 경로")
    args = parser.parse_args()

    image_path = Path(args.image)
    if not image_path.exists():
        logging.error("이미지 파일을 찾을 수 없습니다: %s", image_path)
        return 1

    frame = cv2.imread(str(image_path))
    if frame is None:
        logging.error("이미지 파일을 읽을 수 없습니다: %s", image_path)
        return 1

    detector = Yolo8nDetector(
        model_path=settings.model_path,
        device=settings.inference_device,
        confidence_threshold=settings.inference_confidence_threshold,
        image_size=settings.inference_image_size,
    )
    processor = InferenceProcessor(detector)

    result = processor.process(frame)
    logging.info(
        "frame_shape=%s detections=%d",
        result.frame_shape,
        len(result.detections),
    )

    for detection in result.detections:
        print(
            f"{detection.class_name}({detection.class_id}) "
            f"conf={detection.confidence:.3f} bbox={detection.bbox}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
