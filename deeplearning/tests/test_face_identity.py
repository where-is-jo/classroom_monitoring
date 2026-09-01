from __future__ import annotations

from dataclasses import replace

import numpy as np

from deeplearning.face_degradation import DegradationConfig, degrade_image
from deeplearning.face_identity import (
    FaceGallery,
    FaceIdentityEngine,
    GalleryEntry,
    IdentityStatus,
    IdentityThresholds,
    MultiFaceIdentityTracker,
    TemporalIdentityConsensus,
    _bbox_intersection_over_smaller,
)


def _vector(index: int) -> np.ndarray:
    value = np.zeros(512, dtype=np.float32)
    value[index] = 1.0
    return value


class _Detector:
    def __init__(self, confidence: float = 0.95) -> None:
        self._confidence = confidence

    def detect(
        self, image: np.ndarray, *, max_num: int
    ) -> tuple[np.ndarray, np.ndarray]:
        del image, max_num
        detections = np.asarray(
            [[1.2, 2.2, 20.8, 22.8, self._confidence]], dtype=np.float32
        )
        landmarks = np.asarray(
            [[[5, 7], [15, 7], [10, 12], [6, 18], [14, 18]]], dtype=np.float32
        )
        return detections, landmarks


class _Recognizer:
    def __init__(self, vector: np.ndarray) -> None:
        self._vector = vector

    def get_feat(self, aligned: np.ndarray) -> np.ndarray:
        del aligned
        return self._vector.reshape(1, -1)


def _engine(
    vector: np.ndarray, *, similarity: float, margin: float
) -> FaceIdentityEngine:
    gallery = FaceGallery.from_entries(
        [GalleryEntry("student-a", _vector(0)), GalleryEntry("student-b", _vector(1))]
    )
    return FaceIdentityEngine(
        detector=_Detector(),
        recognizer=_Recognizer(vector),
        gallery=gallery,
        thresholds=IdentityThresholds(similarity, margin),
        minimum_face_size=1,
        preferred_face_size=2,
        minimum_blur_score=0.0,
        preferred_blur_score=1.0,
        uncertain_quality_threshold=0.0,
    )


def test_identify_returns_registered_student_when_both_thresholds_pass() -> None:
    result = _engine(_vector(0), similarity=0.3, margin=0.1).identify(
        np.zeros((30, 30, 3), dtype=np.uint8)
    )

    assert len(result) == 1
    assert result[0].student_id == "student-a"
    assert result[0].bbox == (1, 2, 21, 23)
    assert result[0].similarity == 1.0
    assert result[0].margin == 1.0


def test_identify_returns_unknown_when_similarity_fails() -> None:
    query = (_vector(0) + _vector(1)) / np.sqrt(2.0)

    result = _engine(query, similarity=0.8, margin=0.1).identify(
        np.zeros((30, 30, 3), dtype=np.uint8)
    )

    assert result[0].student_id is None


def test_identify_returns_unknown_when_margin_fails() -> None:
    query = (_vector(0) + _vector(1)) / np.sqrt(2.0)

    result = _engine(query, similarity=0.3, margin=0.1).identify(
        np.zeros((30, 30, 3), dtype=np.uint8)
    )

    assert result[0].student_id is None


def test_empty_image_returns_no_detections() -> None:
    result = _engine(_vector(0), similarity=0.3, margin=0.1).identify(
        np.empty((0, 0, 3), dtype=np.uint8)
    )

    assert result == ()


def test_detection_only_skips_embedding_but_keeps_face_box() -> None:
    engine = _engine(_vector(0), similarity=0.3, margin=0.1)

    result = engine.identify(
        np.zeros((30, 30, 3), dtype=np.uint8),
        extract_embeddings=False,
    )

    assert len(result) == 1
    assert result[0].bbox == (1, 2, 21, 23)
    assert result[0].embedding is None
    assert result[0].status is IdentityStatus.UNCERTAIN
    assert result[0].rejected_reason == "identity_not_scheduled"
    assert engine.last_timings_ms["recognizer"] == 0.0


def test_낮은_confidence_얼굴은_bbox만_유지하고_식별하지_않는다() -> None:
    gallery = FaceGallery.from_entries(
        [GalleryEntry("student-a", _vector(0)), GalleryEntry("student-b", _vector(1))]
    )
    engine = FaceIdentityEngine(
        detector=_Detector(confidence=0.45),
        recognizer=_Recognizer(_vector(0)),
        gallery=gallery,
        thresholds=IdentityThresholds(0.3, 0.1),
        detection_threshold=0.4,
        identity_min_detection_confidence=0.6,
        minimum_face_size=1,
        preferred_face_size=2,
        minimum_blur_score=0.0,
        preferred_blur_score=1.0,
        uncertain_quality_threshold=0.0,
    )

    result = engine.identify(np.zeros((30, 30, 3), dtype=np.uint8))[0]

    assert result.bbox == (1, 2, 21, 23)
    assert result.status is IdentityStatus.UNCERTAIN
    assert result.student_id is None
    assert result.embedding is None
    assert result.rejected_reason == "low_detection_confidence"
    assert engine.last_timings_ms["recognizer"] == 0.0


def test_temporal_consensus_requires_four_matching_votes_in_five_frames() -> None:
    consensus = TemporalIdentityConsensus(window_size=5, consensus_count=4)

    outputs = [
        consensus.update("track-1", value)
        for value in ("student-a", "student-a", None, "student-a", "student-a")
    ]

    assert outputs[:4] == [None, None, None, None]
    assert outputs[4] == "student-a"


def test_low_quality_face_is_uncertain_instead_of_unknown() -> None:
    gallery = FaceGallery.from_entries(
        [GalleryEntry("student-a", _vector(0)), GalleryEntry("student-b", _vector(1))]
    )
    engine = FaceIdentityEngine(
        detector=_Detector(),
        recognizer=_Recognizer(_vector(0)),
        gallery=gallery,
        thresholds=IdentityThresholds(0.3, 0.1),
        minimum_face_size=40,
        preferred_face_size=112,
        minimum_blur_score=20.0,
        preferred_blur_score=100.0,
        uncertain_quality_threshold=0.45,
    )

    result = engine.identify(np.zeros((30, 30, 3), dtype=np.uint8))[0]

    assert result.status is IdentityStatus.UNCERTAIN
    assert result.student_id is None
    assert result.rejected_reason == "low_quality"


def test_tracker_uses_multiple_observations_before_confirming_identity() -> None:
    engine = _engine(_vector(0), similarity=0.3, margin=0.1)
    tracker = MultiFaceIdentityTracker(engine, minimum_observations=2)
    detection = engine.identify(np.zeros((30, 30, 3), dtype=np.uint8))[0]

    first = tracker.update([detection])[0]
    second = tracker.update([detection])[0]

    assert first.status is IdentityStatus.UNCERTAIN
    assert second.status is IdentityStatus.REGISTERED
    assert first.track_id == second.track_id
    assert second.student_id == "student-a"


def test_tracker는_같은_위치의_다른_얼굴을_새_track으로_분리한다() -> None:
    engine = _engine(_vector(0), similarity=0.3, margin=0.1)
    tracker = MultiFaceIdentityTracker(
        engine, minimum_observations=1, track_similarity_threshold=0.8
    )
    first_detection = engine.identify(np.zeros((30, 30, 3), dtype=np.uint8))[0]

    first = tracker.update([first_detection])[0]
    second = tracker.update(
        [replace(first_detection, embedding=_vector(1), student_id="student-b")]
    )[0]

    assert first.track_id != second.track_id
    assert first.student_id == "student-a"
    assert second.student_id == "student-b"


def test_tracker는_현재_얼굴_근거가_없으면_이전_이름을_노출하지_않는다() -> None:
    engine = _engine(_vector(0), similarity=0.3, margin=0.1)
    tracker = MultiFaceIdentityTracker(engine, minimum_observations=1)
    detection = engine.identify(np.zeros((30, 30, 3), dtype=np.uint8))[0]

    confirmed = tracker.update([detection])[0]
    uncertain = tracker.update(
        [replace(detection, embedding=None, quality=0.0, rejected_reason="low_quality")]
    )[0]

    assert confirmed.track_id == uncertain.track_id
    assert uncertain.status is IdentityStatus.UNCERTAIN
    assert uncertain.student_id is None


def test_tracker는_교차하는_얼굴을_embedding으로_일대일_연결한다() -> None:
    engine = _engine(_vector(0), similarity=0.3, margin=0.1)
    tracker = MultiFaceIdentityTracker(
        engine, minimum_observations=1, track_similarity_threshold=0.8
    )
    base = engine.identify(np.zeros((30, 30, 3), dtype=np.uint8))[0]
    student_a_left = replace(base, bbox=(0, 0, 20, 20), embedding=_vector(0))
    student_b_right = replace(
        base, bbox=(80, 0, 100, 20), embedding=_vector(1), student_id="student-b"
    )
    first = tracker.update([student_a_left, student_b_right])

    second = tracker.update(
        [
            replace(student_a_left, bbox=(80, 0, 100, 20)),
            replace(student_b_right, bbox=(0, 0, 20, 20)),
        ]
    )

    assert second[0].track_id == first[0].track_id
    assert second[1].track_id == first[1].track_id


def test_tiled_identification_removes_duplicate_box() -> None:
    engine = _engine(_vector(0), similarity=0.3, margin=0.1)

    results = engine.identify_tiled(
        np.zeros((30, 30, 3), dtype=np.uint8),
        rows=1,
        columns=1,
        include_full_frame=True,
    )

    assert len(results) == 1


def test_nested_face_box_is_detected_as_duplicate_even_with_low_iou() -> None:
    outer = (0, 0, 100, 100)
    inner = (30, 30, 55, 55)

    assert _bbox_intersection_over_smaller(outer, inner) == 1.0


def test_degradation_is_deterministic_and_preserves_shape() -> None:
    image = np.full((40, 50, 3), 128, dtype=np.uint8)
    config = DegradationConfig(
        name="test",
        scale=0.5,
        blur_kernel=3,
        jpeg_quality=50,
        noise_sigma=5.0,
        perspective=0.05,
        brightness=0.7,
    )

    first = degrade_image(image, config, seed=7)
    second = degrade_image(image, config, seed=7)

    assert first.shape == image.shape
    assert np.array_equal(first, second)
