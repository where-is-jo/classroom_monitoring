from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest

from deeplearning.face_identity import FaceGallery, GalleryEntry
from deeplearning.training.face_identification_eval import (
    ProbeImage,
    aggregate_metrics,
    build_gallery_from_directory,
    build_mongo_gallery,
    classify_failure,
    collect_track_pair_similarities,
    evaluate_split,
    load_split,
    score_probe,
    select_threshold_for_far,
    validate_evaluation_inputs,
    write_csv,
    write_thresholds,
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


def test_load_split은_없는_디렉터리를_명확히_거부한다(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="split 디렉터리"):
        load_split(tmp_path / "missing", labeled=False)


def test_평가_split은_각각_비어_있지_않아야_한다(tmp_path: Path) -> None:
    probe = ProbeImage(tmp_path / "one.jpg", "student-a")
    unknown = ProbeImage(tmp_path / "unknown.jpg", None)

    with pytest.raises(ValueError, match="known test"):
        validate_evaluation_inputs(
            _gallery(),
            known_validation=[probe],
            unknown_validation=[unknown],
            known_test=[],
            unknown_test=[ProbeImage(tmp_path / "unknown-test.jpg", None)],
        )


def test_known_평가_학생은_MongoDB_gallery에_등록되어야_한다(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="gallery"):
        validate_evaluation_inputs(
            _gallery(),
            known_validation=[ProbeImage(tmp_path / "one.jpg", "student-missing")],
            unknown_validation=[ProbeImage(tmp_path / "unknown.jpg", None)],
            known_test=[ProbeImage(tmp_path / "test.jpg", "student-a")],
            unknown_test=[ProbeImage(tmp_path / "unknown-test.jpg", None)],
        )


def test_validation과_test에_같은_파일을_재사용할_수_없다(
    tmp_path: Path,
) -> None:
    same = tmp_path / "same.jpg"

    with pytest.raises(ValueError, match="중복"):
        validate_evaluation_inputs(
            _gallery(),
            known_validation=[ProbeImage(same, "student-a")],
            unknown_validation=[ProbeImage(tmp_path / "unknown.jpg", None)],
            known_test=[ProbeImage(same, "student-a")],
            unknown_test=[ProbeImage(tmp_path / "unknown-test.jpg", None)],
        )


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


def test_select_threshold_for_far는_경계_동점으로_목표_FAR을_넘지_않는다() -> None:
    unknown_scores = [0.9, 0.8, 0.8, 0.7]

    threshold = select_threshold_for_far(unknown_scores, target_far=0.5)

    accepted = [score for score in unknown_scores if score >= threshold]
    assert accepted == [0.9]


@pytest.mark.parametrize("target_far", [-0.1, 1.1])
def test_select_threshold_for_far는_잘못된_FAR을_거부한다(
    target_far: float,
) -> None:
    with pytest.raises(ValueError, match="target FAR"):
        select_threshold_for_far([0.5], target_far=target_far)


def test_select_threshold_for_far는_상한의_동점을_배제할_수_없으면_실패한다() -> None:
    with pytest.raises(ValueError, match="목표 FAR"):
        select_threshold_for_far([1.0], target_far=0.0)


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


def test_evaluate_split_rejects_near_tie_below_margin(tmp_path: Path) -> None:
    gallery = _gallery()
    image = ProbeImage(tmp_path / "probe.jpg", "student-a")
    embedding = _vector(0) * 0.71 + _vector(1) * 0.70

    rows = evaluate_split(
        [image],
        lambda path: (embedding, 1.0),
        gallery,
        similarity_threshold=0.5,
        margin_threshold=0.1,
    )

    assert rows[0].predicted_id == "UNKNOWN"
    assert rows[0].failure_type == "false_reject"


def test_build_gallery_from_directory_averages_and_normalizes_per_student(
    tmp_path: Path,
) -> None:
    root = tmp_path / "gallery"
    (root / "student-a").mkdir(parents=True)
    (root / "student-a" / "1.jpg").write_bytes(b"fake")
    (root / "student-a" / "2.jpg").write_bytes(b"fake")
    (root / "student-b").mkdir()
    (root / "student-b" / "1.jpg").write_bytes(b"fake")

    raw_vectors: dict[Path, np.ndarray] = {}
    vector_a1 = np.zeros(512, dtype=np.float32)
    vector_a1[0] = 1.0
    vector_a1[1] = 1.0
    raw_vectors[root / "student-a" / "1.jpg"] = vector_a1
    vector_a2 = np.zeros(512, dtype=np.float32)
    vector_a2[0] = 1.0
    vector_a2[1] = -1.0
    raw_vectors[root / "student-a" / "2.jpg"] = vector_a2
    vector_b1 = np.zeros(512, dtype=np.float32)
    vector_b1[1] = 3.0
    raw_vectors[root / "student-b" / "1.jpg"] = vector_b1

    def embedder(path: Path) -> tuple[np.ndarray, float]:
        return raw_vectors[path], 1.0

    gallery = build_gallery_from_directory(root, embedder)

    by_student = {entry.student_id: entry.vector for entry in gallery.entries}
    assert set(by_student) == {"student-a", "student-b"}
    # student-a: (1,1)/√2와 (1,-1)/√2를 평균하면 (1,0)/√2 방향 -> 재정규화하면 e0
    assert by_student["student-a"] == pytest.approx(_vector(0), abs=1e-6)
    # student-b: (0,3,0,...) 정규화하면 e1
    assert by_student["student-b"] == pytest.approx(_vector(1), abs=1e-6)


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


def test_write_thresholds_creates_runtime_artifact(tmp_path: Path) -> None:
    output_path = tmp_path / "thresholds.json"

    write_thresholds(
        output_path,
        similarity_threshold=0.61,
        margin_threshold=0.08,
        track_similarity_threshold=0.72,
        target_far=0.001,
        track_target_false_association=0.001,
        model_name="arcface",
        model_version="model-v1",
        preprocessing_version="crop-v1",
    )

    assert json.loads(output_path.read_text(encoding="utf-8")) == {
        "similarity_threshold": 0.61,
        "margin_threshold": 0.08,
        "track_similarity_threshold": 0.72,
        "target_far": 0.001,
        "track_target_false_association": 0.001,
        "model_name": "arcface",
        "model_version": "model-v1",
        "preprocessing_version": "crop-v1",
    }


def test_track_임계값용_동일인과_타인_쌍을_분리한다(tmp_path: Path) -> None:
    images = [
        ProbeImage(tmp_path / "a-1.jpg", "student-a"),
        ProbeImage(tmp_path / "a-2.jpg", "student-a"),
        ProbeImage(tmp_path / "b-1.jpg", "student-b"),
        ProbeImage(tmp_path / "b-2.jpg", "student-b"),
    ]
    vectors = {
        "a-1.jpg": _vector(0),
        "a-2.jpg": _vector(0),
        "b-1.jpg": _vector(1),
        "b-2.jpg": _vector(1),
    }

    same, different = collect_track_pair_similarities(
        images, lambda path: (vectors[path.name], 1.0)
    )

    assert same == [1.0, 1.0]
    assert different == [0.0, 0.0, 0.0, 0.0]


def test_write_thresholds는_런타임이_거부할_값을_쓰지_않는다(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="similarity"):
        write_thresholds(
            tmp_path / "thresholds.json",
            similarity_threshold=1.1,
            margin_threshold=0.1,
            track_similarity_threshold=0.7,
            target_far=0.001,
            track_target_false_association=0.001,
            model_name="arcface",
            model_version="model-v1",
            preprocessing_version="crop-v1",
        )

    assert not (tmp_path / "thresholds.json").exists()


def test_mongodb_gallery는_URI가_없으면_연결_전에_거부한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in ("MONGODB_URI", "DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MONGODB_DATABASE", "classroom")

    with pytest.raises(RuntimeError, match="MONGODB_URI"):
        build_mongo_gallery()


def test_mongodb_gallery는_DB_이름이_없으면_연결_전에_거부한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MONGODB_URI", "mongodb://example.invalid")
    for key in ("MONGODB_DATABASE", "DATABASE_NAME"):
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(RuntimeError, match="MONGODB_DATABASE"):
        build_mongo_gallery()
