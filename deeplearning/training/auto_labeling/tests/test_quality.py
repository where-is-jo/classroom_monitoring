from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from auto_labeling.quality import inspect_frame_quality


def test_frame_quality_accepts_normal_image(tmp_path: Path) -> None:
    rng = np.random.default_rng(42)
    image = rng.integers(20, 220, size=(120, 160, 3), dtype=np.uint8)
    path = tmp_path / "normal.jpg"
    assert cv2.imwrite(str(path), image)

    report = inspect_frame_quality(path)

    assert report["passed"] is True
    assert report["reasons"] == []


def test_frame_quality_rejects_green_transmission_failure(tmp_path: Path) -> None:
    image = np.zeros((120, 160, 3), dtype=np.uint8)
    image[:20] = (80, 80, 80)
    image[20:] = (0, 128, 0)
    path = tmp_path / "green.jpg"
    assert cv2.imwrite(str(path), image)

    report = inspect_frame_quality(path)

    assert report["passed"] is False
    assert "dominant-green-corruption" in report["reasons"]


def test_frame_quality_rejects_black_frame(tmp_path: Path) -> None:
    path = tmp_path / "black.jpg"
    assert cv2.imwrite(str(path), np.zeros((120, 160, 3), dtype=np.uint8))

    report = inspect_frame_quality(path)

    assert report["passed"] is False
    assert "near-black-frame" in report["reasons"]


def test_frame_quality_rejects_partial_horizontal_corruption(tmp_path: Path) -> None:
    rng = np.random.default_rng(7)
    image = rng.integers(20, 220, size=(160, 200, 3), dtype=np.uint8)
    image[120:] = (0, 128, 0)
    path = tmp_path / "green-bottom-band.jpg"
    assert cv2.imwrite(str(path), image)

    report = inspect_frame_quality(path)

    assert report["passed"] is False
    assert "dominant-green-horizontal-band" in report["reasons"]


def test_frame_quality_rejects_bottom_texture_collapse(tmp_path: Path) -> None:
    rng = np.random.default_rng(11)
    image = rng.integers(20, 220, size=(160, 200, 3), dtype=np.uint8)
    gradient = np.linspace(70, 150, 200, dtype=np.uint8)
    image[120:] = np.repeat(gradient[None, :, None], 40, axis=0)
    path = tmp_path / "smooth-bottom-band.jpg"
    assert cv2.imwrite(str(path), image)

    report = inspect_frame_quality(path)

    assert report["passed"] is False
    assert "bottom-texture-collapse" in report["reasons"]
