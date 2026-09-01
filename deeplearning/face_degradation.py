"""저화질 카메라 조건을 재현하기 위한 결정적 이미지 열화 도구."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class DegradationConfig:
    name: str
    scale: float = 1.0
    blur_kernel: int = 1
    jpeg_quality: int = 100
    noise_sigma: float = 0.0
    perspective: float = 0.0
    brightness: float = 1.0


DEFAULT_DEGRADATIONS = (
    DegradationConfig("original"),
    DegradationConfig("mild", 0.65, 3, 70, 3.0, 0.02, 0.85),
    DegradationConfig("medium", 0.40, 5, 45, 7.0, 0.05, 0.70),
    DegradationConfig("severe", 0.25, 7, 25, 12.0, 0.08, 0.55),
)


def degrade_image(
    image_bgr: np.ndarray,
    config: DegradationConfig,
    *,
    seed: int = 0,
) -> np.ndarray:
    if image_bgr is None or image_bgr.size == 0:
        raise ValueError("열화할 이미지가 비어 있습니다.")
    if not 0.0 < config.scale <= 1.0:
        raise ValueError("scale은 0보다 크고 1 이하여야 합니다.")

    height, width = image_bgr.shape[:2]
    result = image_bgr.copy()
    if config.scale < 1.0:
        small = cv2.resize(
            result,
            (max(1, int(width * config.scale)), max(1, int(height * config.scale))),
            interpolation=cv2.INTER_AREA,
        )
        result = cv2.resize(small, (width, height), interpolation=cv2.INTER_LINEAR)

    if config.perspective > 0:
        shift = width * config.perspective
        source = np.float32([[0, 0], [width, 0], [width, height], [0, height]])
        target = np.float32(
            [[shift, 0], [width - shift, shift], [width, height], [0, height]]
        )
        matrix = cv2.getPerspectiveTransform(source, target)
        result = cv2.warpPerspective(result, matrix, (width, height))

    kernel = max(1, int(config.blur_kernel))
    if kernel > 1:
        kernel += 1 - kernel % 2
        result = cv2.GaussianBlur(result, (kernel, kernel), 0)

    result = np.clip(result.astype(np.float32) * config.brightness, 0, 255)
    if config.noise_sigma > 0:
        rng = np.random.default_rng(seed)
        result += rng.normal(0, config.noise_sigma, result.shape)
    result = np.clip(result, 0, 255).astype(np.uint8)

    if config.jpeg_quality < 100:
        ok, encoded = cv2.imencode(
            ".jpg",
            result,
            [cv2.IMWRITE_JPEG_QUALITY, int(config.jpeg_quality)],
        )
        if not ok:
            raise RuntimeError("JPEG 열화 생성에 실패했습니다.")
        result = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    return result
