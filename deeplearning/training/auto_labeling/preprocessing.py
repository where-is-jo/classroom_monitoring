from __future__ import annotations

from typing import Any, cast

import cv2
import numpy as np
from numpy.typing import NDArray

from .errors import AutoLabelingError

UNIFORM_FULL_FRAME_PIXELATION = "uniform-full-frame-pixelation-v1"
PERSON_BBOX_TOP_PIXELATION = "person-bbox-top-pixelation-v1"
LEGACY_COMBINED_PIXELATION = "base-preserved-plus-person-bbox-top-pixelation-v1"
DEFAULT_PIXELATION_BLOCK_SIZE = 8


def uniform_pixelation_contract(
    block_size: int = DEFAULT_PIXELATION_BLOCK_SIZE,
) -> dict[str, object]:
    """N1 학습·평가·추론에 공통으로 적용할 라벨 독립 계약을 만든다."""

    if not 2 <= block_size <= 32:
        raise AutoLabelingError("픽셀화 블록 크기는 2~32여야 합니다.")
    return {
        "schema_version": 1,
        "method": UNIFORM_FULL_FRAME_PIXELATION,
        "label_derived": False,
        "training_compatible": True,
        "inference_preprocessing_required": True,
        "pixelation_block_size": block_size,
    }


def apply_training_preprocessing(
    image: NDArray[Any], contract: dict[str, object]
) -> NDArray[np.uint8]:
    """학습·검증·자동 라벨링·실제 추론에 같은 픽셀화를 적용한다."""

    method = contract.get("method")
    if method != UNIFORM_FULL_FRAME_PIXELATION:
        raise AutoLabelingError(
            "정답 박스 기반 전처리는 실제 추론에서 재현할 수 없습니다."
        )
    block_size = contract.get("pixelation_block_size")
    if not isinstance(block_size, int) or not 2 <= block_size <= 32:
        raise AutoLabelingError("픽셀화 블록 크기가 올바르지 않습니다.")
    if image.ndim != 3 or image.shape[2] != 3 or image.size == 0:
        raise AutoLabelingError(
            "전처리 입력 이미지는 비어 있지 않은 BGR 이미지여야 합니다."
        )
    height, width = image.shape[:2]
    small = cv2.resize(
        image,
        (max(1, width // block_size), max(1, height // block_size)),
        interpolation=cv2.INTER_AREA,
    )
    return cast(
        NDArray[np.uint8],
        cv2.resize(small, (width, height), interpolation=cv2.INTER_NEAREST),
    )
