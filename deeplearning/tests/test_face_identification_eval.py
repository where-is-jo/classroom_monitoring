from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest

from deeplearning.face_identity import FaceGallery, GalleryEntry
from deeplearning.training.face_identification_eval import (
    ProbeImage,
    aggregate_metrics,
    classify_failure,
    evaluate_split,
    load_split,
    score_probe,
    select_threshold_for_far,
    write_csv,
)


def _vector(index: int, dimension: int = 512) -> np.ndarray:
    value = np.zeros(dimension, dtype=np.float32)
    value[index] = 1.0
    return value


def _gallery() -> FaceGallery:
    return FaceGallery.from_entries(
        [
            GalleryEntry("student-a", _vector(0)),
            GalleryEntry("student-b", _vector(1)),
        ]
    )


def test_load_split_reads_known_directory_structure(tmp_path: Path) -> None:
    root = tmp_path / "known"
    (root / "student-a").mkdir(parents=True)
    (root / "student-a" / "one.jpg").write_bytes(b"fake")
    (root / "student-a" / "two.png").write_bytes(b"fake")
    (root / "student-b").mkdir()
    (root / "student-b" / "one.jpeg").write_bytes(b"fake")

    images = load_split(root, labeled=True)

    assert {image.true_id for image in images} == {"student-a", "student-b"}
    assert len(images) == 3


def test_load_split_reads_unknown_flat_directory(tmp_path: Path) -> None:
    root = tmp_path / "unknown"
    root.mkdir()
    (root / "one.jpg").write_bytes(b"fake")
    (root / "two.jpg").write_bytes(b"fake")

    images = load_split(root, labeled=False)

    assert len(images) == 2
    assert all(image.true_id is None for image in images)


def test_score_probe_returns_top1_and_top2(tmp_path: Path) -> None:
    gallery = _gallery()
    image = ProbeImage(tmp_path / "probe.jpg", "student-a")

    def embedder(path: Path) -> tuple[np.ndarray, float]:
        del path
        return _vector(0), 12.5

    score = score_probe(image, embedder, gallery)

    assert score is not None
    assert score.predicted_top1_id == "student-a"
    assert score.top1_cosine == pytest.approx(1.0)
    assert score.top2_cosine == pytest.approx(0.0)
    assert score.recognition_ms == pytest.approx(12.5)


def test_score_probe_returns_none_when_face_not_found(tmp_path: Path) -> None:
    gallery = _gallery()
    image = ProbeImage(tmp_path / "probe.jpg", "student-a")

    def embedder(path: Path) -> tuple[None, float]:
        del path
        return None, 0.0

    assert score_probe(image, embedder, gallery) is None


@pytest.mark.parametrize(
    ("true_id", "predicted_id", "expected"),
    [
        ("UNKNOWN", "UNKNOWN", "correct_reject"),
        ("UNKNOWN", "student-a", "false_accept"),
        ("student-a", "UNKNOWN", "false_reject"),
        ("student-a", "student-a", "correct_match"),
        ("student-a", "student-b", "wrong_identity"),
    ],
)
def test_classify_failure(true_id: str, predicted_id: str, expected: str) -> None:
    assert classify_failure(true_id, predicted_id) == expected


def test_select_threshold_for_far_excludes_top_scores_up_to_target() -> None:
    unknown_scores = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.0]

    threshold = select_threshold_for_far(unknown_scores, target_far=0.2)

    accepted = [score for score in unknown_scores if score >= threshold]
    assert len(accepted) == 2


def test_select_threshold_for_far_rejects_all_when_target_is_zero() -> None:
    unknown_scores = [0.9, 0.5, 0.1]

    threshold = select_threshold_for_far(unknown_scores, target_far=0.0)

    assert all(score < threshold for score in unknown_scores)


def test_select_threshold_for_far_raises_on_empty_input() -> None:
    with pytest.raises(ValueError):
        select_threshold_for_far([], target_far=0.001)


def test_evaluate_split_and_aggregate_metrics(tmp_path: Path) -> None:
    gallery = _gallery()
    images = [
        ProbeImage(tmp_path / "a1.jpg", "student-a"),
        ProbeImage(tmp_path / "a2.jpg", "student-a"),
        ProbeImage(tmp_path / "u1.jpg", None),
        ProbeImage(tmp_path / "u2.jpg", None),
        ProbeImage(tmp_path / "missing.jpg", "student-a"),
    ]
    # a2는 student-a와 방향은 가깝지만(top1 cosine 0.6) threshold(0.9)에는
    # 못 미쳐 등록자를 UNKNOWN으로 놓치는 경우(false_reject)를 만든다.
    a2_embedding = np.zeros(512, dtype=np.float32)
    a2_embedding[0] = 0.6
    a2_embedding[2] = 0.8
    embeddings = {
        tmp_path / "a1.jpg": _vector(0),
        tmp_path / "a2.jpg": a2_embedding,
        tmp_path / "u1.jpg": _vector(0) * 0.5 + _vector(1) * 0.5,
        tmp_path / "u2.jpg": np.full(512, -1.0, dtype=np.float32),
        tmp_path / "missing.jpg": None,
    }

    def embedder(path: Path) -> tuple[np.ndarray | None, float]:
        embedding = embeddings[path]
        return (embedding, 5.0) if embedding is not None else (None, 0.0)

    rows = evaluate_split(images, embedder, gallery, similarity_threshold=0.9)

    by_image = {row.image_id: row for row in rows}
    assert by_image["a1.jpg"].failure_type == "correct_match"
    assert by_image["a2.jpg"].failure_type == "false_reject"
    assert by_image["u1.jpg"].failure_type == "correct_reject"
    assert by_image["u2.jpg"].failure_type == "correct_reject"
    assert by_image["missing.jpg"].failure_type == "face_not_detected"

    metrics = aggregate_metrics(rows)
    assert metrics.registered_probe_count == 3
    assert metrics.unknown_probe_count == 2
    assert metrics.registered_success_rate == pytest.approx(1 / 3)
    assert metrics.registered_false_reject_rate == pytest.approx(1 / 3)
    assert metrics.unknown_false_accept_rate == pytest.approx(0.0)
    assert metrics.unknown_correct_reject_rate == pytest.approx(1.0)
    assert metrics.average_recognition_ms == pytest.approx(5.0)


def test_write_csv_round_trip(tmp_path: Path) -> None:
    gallery = _gallery()
    images = [ProbeImage(tmp_path / "a1.jpg", "student-a")]

    def embedder(path: Path) -> tuple[np.ndarray, float]:
        del path
        return _vector(0), 3.0

    rows = evaluate_split(images, embedder, gallery, similarity_threshold=0.5)
    output_path = tmp_path / "out" / "eval.csv"

    write_csv(rows, output_path)

    with output_path.open(newline="", encoding="utf-8") as handle:
        reader = list(csv.reader(handle))
    assert reader[0] == [
        "image_id",
        "true_id",
        "predicted_id",
        "top1_cosine",
        "top2_cosine",
        "decision",
        "failure_type",
        "recognition_ms",
    ]
    assert reader[1][0] == "a1.jpg"
    assert reader[1][6] == "correct_match"
