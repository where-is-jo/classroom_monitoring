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
    # 채널 0(B)=0 -> -1.0, 채널1(G)=255 -> 1.0, 채널2(R)=128 -> 128/255*2-1 근사
    assert tensor[0, 0, 0, 0] == pytest.approx(-1.0)
    assert tensor[0, 1, 0, 0] == pytest.approx(1.0)
    # float32 정밀도라 기본 rel=1e-6로는 너무 빡빡함 - abs 오차를 명시적으로 허용한다.
    assert tensor[0, 2, 0, 0] == pytest.approx((128 / 255.0 - 0.5) / 0.5, abs=1e-6)


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
