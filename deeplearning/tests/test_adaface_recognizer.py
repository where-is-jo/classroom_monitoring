from __future__ import annotations

import numpy as np
import pytest

from deeplearning.training.adaface_recognizer import preprocess_aligned_face


def test_preprocess_maps_pixel_range_to_minus_one_to_one() -> None:
    aligned = np.zeros((112, 112, 3), dtype=np.uint8)
    aligned[:, :, 0] = 0
    aligned[:, :, 1] = 255
    aligned[:, :, 2] = 128

    tensor = preprocess_aligned_face(aligned)

    assert tensor.shape == (1, 3, 112, 112)
    assert tensor.dtype == np.float32
    # 운영 모델은 RGB 계약이므로 입력 BGR의 채널 순서가 뒤집힌다.
    assert tensor[0, 0, 0, 0] == pytest.approx((128 / 255.0 - 0.5) / 0.5, abs=1e-6)
    assert tensor[0, 1, 0, 0] == pytest.approx(1.0)
    assert tensor[0, 2, 0, 0] == pytest.approx(-1.0)


def test_preprocess_transposes_hwc_to_chw() -> None:
    aligned = np.zeros((112, 112, 3), dtype=np.uint8)
    aligned[5, 7, 1] = 255  # (row=5, col=7)의 G 채널

    tensor = preprocess_aligned_face(aligned)

    assert tensor[0, 1, 5, 7] == pytest.approx(1.0)


def test_preprocess_rejects_wrong_size() -> None:
    aligned = np.zeros((100, 100, 3), dtype=np.uint8)

    with pytest.raises(ValueError):
        preprocess_aligned_face(aligned)


def test_preprocess_rejects_wrong_channel_count() -> None:
    aligned = np.zeros((112, 112, 4), dtype=np.uint8)

    with pytest.raises(ValueError):
        preprocess_aligned_face(aligned)
