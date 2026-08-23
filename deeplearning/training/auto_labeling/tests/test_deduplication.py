from __future__ import annotations

import shutil
from pathlib import Path

import cv2
import numpy as np
import pytest

from auto_labeling.core import load_settings, sha256_file
from auto_labeling.deduplication import DeduplicationInput, deduplicate_frames
from auto_labeling.errors import AutoLabelingError
from auto_labeling.publish import _assign_session_splits


def test_exact_duplicate_across_cameras_prefers_human_review(tmp_path: Path) -> None:
    first_image = tmp_path / "auto.jpg"
    second_image = tmp_path / "human.jpg"
    _write_scene(first_image)
    shutil.copy2(first_image, second_image)
    first_label = _write_label(tmp_path / "auto.txt")
    second_label = _write_label(tmp_path / "human.txt")

    result = deduplicate_frames(
        [
            _input(
                "frame-auto",
                "camera-001",
                first_image,
                first_label,
                "calibrated-auto-accept",
            ),
            _input(
                "frame-human",
                "camera-002",
                second_image,
                second_label,
                "human-reviewed",
            ),
        ],
        load_settings(),
    )

    assert result.retained_frame_ids == ("frame-human",)
    assert result.removed_frame_count == 1
    assert result.report_rows[0]["representative_selection_reason"] == "human-reviewed"
    assert result.report_rows[0]["duplicates"][0]["match_type"] == "exact-sha256"


def test_reencoded_near_duplicate_is_removed_only_for_same_camera(
    tmp_path: Path,
) -> None:
    first_image = tmp_path / "first.jpg"
    second_image = tmp_path / "second.jpg"
    _write_scene(first_image, jpeg_quality=95)
    image = cv2.imread(str(first_image))
    assert image is not None
    assert cv2.imwrite(str(second_image), image, [cv2.IMWRITE_JPEG_QUALITY, 82])
    first_label = _write_label(tmp_path / "first.txt")
    second_label = _write_label(tmp_path / "second.txt")
    settings = load_settings()

    same_camera = deduplicate_frames(
        [
            _input("frame-a", "camera-001", first_image, first_label),
            _input("frame-b", "camera-001", second_image, second_label),
        ],
        settings,
    )
    different_cameras = deduplicate_frames(
        [
            _input("frame-a", "camera-001", first_image, first_label),
            _input("frame-b", "camera-002", second_image, second_label),
        ],
        settings,
    )

    assert same_camera.removed_frame_count == 1
    assert same_camera.report_rows[0]["duplicates"][0]["match_type"] == (
        "visual-same-camera"
    )
    assert different_cameras.retained_frame_count == 2
    assert different_cameras.report_rows == ()


def test_visual_similarity_does_not_remove_different_bbox(tmp_path: Path) -> None:
    first_image = tmp_path / "first.jpg"
    second_image = tmp_path / "second.jpg"
    _write_scene(first_image, jpeg_quality=95)
    image = cv2.imread(str(first_image))
    assert image is not None
    assert cv2.imwrite(str(second_image), image, [cv2.IMWRITE_JPEG_QUALITY, 82])
    first_label = _write_label(tmp_path / "first.txt")
    second_label = _write_label(
        tmp_path / "second.txt",
        content="0 0.20000000 0.20000000 0.10000000 0.10000000\n",
    )

    result = deduplicate_frames(
        [
            _input("frame-a", "camera-001", first_image, first_label),
            _input("frame-b", "camera-001", second_image, second_label),
        ],
        load_settings(),
    )

    assert result.retained_frame_count == 2


def test_exact_duplicate_with_conflicting_labels_is_rejected(tmp_path: Path) -> None:
    first_image = tmp_path / "first.jpg"
    second_image = tmp_path / "second.jpg"
    _write_scene(first_image)
    shutil.copy2(first_image, second_image)
    first_label = _write_label(tmp_path / "first.txt")
    second_label = _write_label(
        tmp_path / "second.txt",
        content="0 0.20000000 0.20000000 0.10000000 0.10000000\n",
    )

    with pytest.raises(AutoLabelingError, match="서로 다른 검수 라벨"):
        deduplicate_frames(
            [
                _input("frame-a", "camera-001", first_image, first_label),
                _input("frame-b", "camera-002", second_image, second_label),
            ],
            load_settings(),
        )


def test_sharper_visual_duplicate_is_representative(tmp_path: Path) -> None:
    sharp_image = tmp_path / "sharp.jpg"
    soft_image = tmp_path / "soft.jpg"
    _write_scene(sharp_image)
    image = cv2.imread(str(sharp_image))
    assert image is not None
    softened = cv2.GaussianBlur(image, (3, 3), 0)
    assert cv2.imwrite(str(soft_image), softened, [cv2.IMWRITE_JPEG_QUALITY, 95])
    sharp_label = _write_label(tmp_path / "sharp.txt")
    soft_label = _write_label(tmp_path / "soft.txt")

    result = deduplicate_frames(
        [
            _input("frame-a-soft", "camera-001", soft_image, soft_label),
            _input("frame-z-sharp", "camera-001", sharp_image, sharp_label),
        ],
        load_settings(),
    )

    assert result.retained_frame_ids == ("frame-z-sharp",)
    assert result.report_rows[0]["representative_selection_reason"] == (
        "highest-sharpness"
    )


def test_retained_sessions_are_split_deterministically() -> None:
    frames = [
        {"session_id": f"session-{index:02d}"} for index in range(10) for _ in range(2)
    ]

    first = _assign_session_splits(frames)
    second = _assign_session_splits(list(reversed(frames)))

    assert first == second
    assert list(first.values()).count("train") == 9
    assert list(first.values()).count("val") == 1
    assert "test" not in first.values()


def _input(
    frame_id: str,
    camera_id: str,
    image_path: Path,
    label_path: Path,
    approval_type: str = "human-reviewed",
) -> DeduplicationInput:
    return DeduplicationInput(
        frame_id=frame_id,
        camera_id=camera_id,
        session_id=f"session-{frame_id}",
        image_path=image_path,
        label_path=label_path,
        image_sha256=sha256_file(image_path),
        approval_type=approval_type,
    )


def _write_scene(path: Path, *, jpeg_quality: int = 95) -> None:
    image = np.full((128, 160, 3), 176, dtype=np.uint8)
    cv2.rectangle(image, (12, 10), (148, 118), (60, 60, 60), 2)
    cv2.rectangle(image, (48, 22), (108, 112), (230, 230, 230), -1)
    cv2.circle(image, (78, 42), 13, (30, 30, 30), -1)
    cv2.putText(
        image,
        "synthetic",
        (18, 105),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (0, 0, 0),
        1,
    )
    assert cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])


def _write_label(path: Path, *, content: str | None = None) -> Path:
    path.write_text(
        content or "0 0.50000000 0.50000000 0.40000000 0.70000000\n",
        encoding="utf-8",
    )
    return path
