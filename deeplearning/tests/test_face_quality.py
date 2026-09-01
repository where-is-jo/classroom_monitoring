import time

import cv2
import numpy as np
import pytest

from deeplearning.app import (
    _face_quality,
    _fingerprint_history,
    _frame_history,
    _temporal_quality,
    discard_session,
)


def test_sharp_face_scores_higher_than_blurred_face() -> None:
    sharp = np.zeros((120, 120, 3), dtype=np.uint8)
    sharp[:, ::4] = 255
    blurred = cv2.GaussianBlur(sharp, (31, 31), 0)

    sharp_score, _, _ = _face_quality(sharp)
    blurred_score, _, _ = _face_quality(blurred)

    assert sharp_score > blurred_score


def test_extreme_light_scores_lower_than_usable_light() -> None:
    usable = np.full((80, 80, 3), 128, dtype=np.uint8)
    dark = np.zeros((80, 80, 3), dtype=np.uint8)

    _, usable_score, _ = _face_quality(usable)
    _, dark_score, _ = _face_quality(dark)

    assert usable_score == 1
    assert dark_score == 0


def test_temporal_quality_detects_identical_and_fast_frames() -> None:
    enrollment_id = "quality-test"
    now = time.monotonic()
    _temporal_quality(
        enrollment_id,
        yaw=0,
        pitch=0,
        fingerprint=123,
        captured_at=now,
    )

    speed, duplicate = _temporal_quality(
        enrollment_id,
        yaw=30,
        pitch=0,
        fingerprint=123,
        captured_at=now + 0.1,
    )

    assert speed == pytest.approx(300)
    assert duplicate == 0
    _, duplicate = _temporal_quality(
        enrollment_id,
        yaw=30,
        pitch=0,
        fingerprint=123,
        captured_at=now + 0.2,
    )
    assert duplicate == 1
    discard_session(enrollment_id)
    assert enrollment_id not in _frame_history
    assert enrollment_id not in _fingerprint_history


def test_temporal_quality_detects_return_to_previous_same_pose() -> None:
    enrollment_id = "duplicate-gallery"
    _temporal_quality(enrollment_id, yaw=10, pitch=0, fingerprint=123, captured_at=1)
    _temporal_quality(enrollment_id, yaw=25, pitch=0, fingerprint=999, captured_at=2)

    _, duplicate = _temporal_quality(
        enrollment_id,
        yaw=11,
        pitch=0,
        fingerprint=123,
        captured_at=3,
    )

    assert duplicate == 1
    discard_session(enrollment_id)
