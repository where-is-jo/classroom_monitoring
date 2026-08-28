from __future__ import annotations

import cv2
import numpy as np
import pytest

from auto_labeling.errors import AutoLabelingError
from auto_labeling.preprocessing import (
    apply_training_preprocessing,
    original_frame_contract,
    uniform_pixelation_contract,
)


def test_uniform_pixelation_contract_is_label_independent() -> None:
    contract = uniform_pixelation_contract(8)

    assert contract == {
        "schema_version": 1,
        "method": "uniform-full-frame-pixelation-v1",
        "label_derived": False,
        "training_compatible": True,
        "inference_preprocessing_required": True,
        "pixelation_block_size": 8,
    }


def test_training_preprocessing_matches_pixel8_reference() -> None:
    image = np.arange(12 * 16 * 3, dtype=np.uint8).reshape(12, 16, 3)
    expected_small = cv2.resize(image, (2, 1), interpolation=cv2.INTER_AREA)
    expected = cv2.resize(
        expected_small,
        (16, 12),
        interpolation=cv2.INTER_NEAREST,
    )

    actual = apply_training_preprocessing(image, uniform_pixelation_contract(8))

    assert actual.shape == image.shape
    assert np.array_equal(actual, expected)


def test_original_frame_contract_does_not_require_inference_preprocessing() -> None:
    assert original_frame_contract() == {
        "schema_version": 1,
        "method": "original-frame-v1",
        "label_derived": False,
        "training_compatible": True,
        "inference_preprocessing_required": False,
    }


def test_original_frame_preprocessing_preserves_pixels() -> None:
    image = np.arange(12 * 16 * 3, dtype=np.uint8).reshape(12, 16, 3)

    actual = apply_training_preprocessing(image, original_frame_contract())

    assert np.array_equal(actual, image)


@pytest.mark.parametrize("block_size", [1, 33])
def test_uniform_pixelation_rejects_unsupported_block_size(block_size: int) -> None:
    with pytest.raises(AutoLabelingError, match="2~32"):
        uniform_pixelation_contract(block_size)
