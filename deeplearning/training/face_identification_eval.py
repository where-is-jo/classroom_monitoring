"""얼굴 식별 임베딩 모델(ArcFace/AdaFace) 공정 비교용 평가 하네스.

Notion `디텍팅 모델 비교` 문서가 PM 몫으로 남긴 4가지 — validation/test 고정
분리, 이미지별 CSV, recognition_ms 측정, 고정 test set 최종 지표 — 를 채운다.
SCRFD 검출·정렬과 임계값 결정 로직은 `deeplearning.face_identity`를 그대로
쓰고, 이 모듈은 그 위에서 고정 이미지 split을 돌려 CSV와 집계 지표를 만드는
역할만 한다.

다른 임베딩 모델(AdaFace 등)과 비교할 때는 `FACE_RECOGNITION_MODEL_PATH`와
`FACE_EMBEDDING_MODEL_NAME`만 바꿔 같은 스크립트를 재사용한다. 등록 학생
gallery 벡터는 모델별로 공간이 다르므로 항상 같은 원본 얼굴 crop에서
새로 만든 벡터를 써야 한다 — MongoDB의 `FACE_EMBEDDING_COLLECTION`도
모델별로 분리해서 지정한다.
"""

from __future__ import annotations

import csv
import json
import math
import os
import time
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Protocol

import numpy as np

from deeplearning.face_identification import (
    FaceGalleryUnavailable,
    FaceModelMetadata,
    MongoFaceGalleryLoader,
)
from deeplearning.face_identity import FaceGallery, GalleryEntry, normalize_embedding

UNKNOWN_LABEL = "UNKNOWN"

_IMAGE_EXTENSIONS = ("*.jpg", "*.jpeg", "*.png")


@dataclass(frozen=True)
class ProbeImage:
    """평가 대상 이미지 한 장. true_id가 None이면 미등록(unknown) 확인용이다."""

    path: Path
    true_id: str | None


@dataclass(frozen=True)
class ProbeScore:
    """gallery 대비 top-1/top-2 cosine과 추론 시간. 아직 threshold를 적용하지 않은 raw 값이다."""

    path: Path
    true_id: str | None
    predicted_top1_id: str
    top1_cosine: float
    top2_cosine: float
    recognition_ms: float


@dataclass(frozen=True)
class EvalRow:
    """CSV 한 줄. `image_id,true_id,predicted_id,top1_cosine,top2_cosine,decision,failure_type,recognition_ms`."""

    image_id: str
    true_id: str
    predicted_id: str
    top1_cosine: float
    top2_cosine: float
    decision: str
    failure_type: str
    recognition_ms: float


@dataclass(frozen=True)
class AggregateMetrics:
    """디텍팅 모델 비교 문서가 지정한 최소 5개 지표."""

    registered_success_rate: float
    registered_false_reject_rate: float
    unknown_false_accept_rate: float
    unknown_correct_reject_rate: float
    average_recognition_ms: float
    registered_probe_count: int
    unknown_probe_count: int


class Embedder(Protocol):
    def __call__(self, image_path: Path) -> tuple[np.ndarray | None, float]:
        """이미지에서 얼굴 하나를 검출·정렬·embedding하고 (embedding, recognition_ms)를 반환한다.

        얼굴을 찾지 못하면 (None, 0.0)을 반환한다.
        """


def load_split(root: Path, *, labeled: bool) -> list[ProbeImage]:
    """평가 이미지 목록을 만든다.

    labeled=True면 `root/<student_id>/*.jpg` 구조에서 true_id를 폴더명으로 채운다(known).
    labeled=False면 root 바로 아래 이미지 전부를 true_id=None으로 채운다(unknown).
    """
    if not root.is_dir():
        raise FileNotFoundError(f"얼굴 평가 split 디렉터리를 찾을 수 없습니다: {root}")
    images: list[ProbeImage] = []
    if labeled:
        for student_dir in sorted(path for path in root.iterdir() if path.is_dir()):
            for image_path in sorted(_iter_images(student_dir)):
                images.append(ProbeImage(image_path, student_dir.name))
    else:
        for image_path in sorted(_iter_images(root)):
            images.append(ProbeImage(image_path, None))
    return images


def validate_evaluation_inputs(
    gallery: FaceGallery,
    *,
    known_validation: list[ProbeImage],
    unknown_validation: list[ProbeImage],
    known_test: list[ProbeImage],
    unknown_test: list[ProbeImage],
) -> None:
    """빈 split·gallery 불일치·validation/test 재사용을 산출물 생성 전에 막는다."""
    splits = {
        "known validation": known_validation,
        "unknown validation": unknown_validation,
        "known test": known_test,
        "unknown test": unknown_test,
    }
    empty_splits = [name for name, images in splits.items() if not images]
    if empty_splits:
        raise ValueError("얼굴 평가 split이 비어 있습니다: " + ", ".join(empty_splits))

    gallery_ids = {entry.student_id for entry in gallery.entries}
    known_ids = {
        image.true_id
        for image in known_validation + known_test
        if image.true_id is not None
    }
    if not known_ids <= gallery_ids:
        # 학생 ID 목록은 오류 메시지나 CI 로그로 내보내지 않는다.
        raise ValueError(
            "known 평가 학생이 MongoDB gallery에 모두 등록되어 있지 않습니다."
        )

    resolved_paths = [
        image.path.resolve() for images in splits.values() for image in images
    ]
    if len(resolved_paths) != len(set(resolved_paths)):
        raise ValueError("validation과 test split에 같은 이미지 파일이 중복되었습니다.")


def _iter_images(directory: Path) -> Iterable[Path]:
    for pattern in _IMAGE_EXTENSIONS:
        yield from directory.glob(pattern)


def score_probe(
    image: ProbeImage,
    embedder: Embedder,
    gallery: FaceGallery,
) -> ProbeScore | None:
    """gallery 대비 top-1/top-2 cosine을 계산한다. 얼굴을 찾지 못하면 None."""
    embedding, recognition_ms = embedder(image.path)
    if embedding is None:
        return None
    scores = gallery.matrix @ normalize_embedding(embedding)
    order = np.argsort(scores)[::-1]
    top1_index = int(order[0])
    top1_cosine = float(scores[top1_index])
    top2_cosine = float(scores[int(order[1])]) if len(order) > 1 else -1.0
    return ProbeScore(
        path=image.path,
        true_id=image.true_id,
        predicted_top1_id=gallery.entries[top1_index].student_id,
        top1_cosine=top1_cosine,
        top2_cosine=top2_cosine,
        recognition_ms=recognition_ms,
    )


def select_threshold_for_far(
    unknown_top1_cosines: Iterable[float],
    *,
    target_far: float = 0.001,
    maximum_threshold: float = 1.0,
) -> float:
    """미등록(unknown) validation의 top-1 cosine 분포에서 목표 FAR 이하를 만족하는
    가장 낮은(관대한) similarity threshold를 고른다.

    경계 점수에 동점(tie)이 있으면 경계 바로 위의 representable float를 사용해 실제
    허용 수가 목표를 넘지 않게 한다. 런타임 범위 안에서 경계 점수를 배제할 수 없으면
    임의 파일을 만들지 않고 실패한다.
    """
    values = sorted(unknown_top1_cosines, reverse=True)
    if not 0.0 <= target_far <= 1.0:
        raise ValueError("target FAR은 0과 1 사이여야 합니다.")
    if maximum_threshold <= 0.0:
        raise ValueError("threshold 상한은 0보다 커야 합니다.")
    if not values:
        raise ValueError("unknown validation 점수가 비어 있습니다.")
    if any(
        not math.isfinite(value) or not -1.0 <= value <= maximum_threshold
        for value in values
    ):
        raise ValueError("validation 점수가 임계값 범위를 벗어났습니다.")
    allowed_false_accepts = math.floor(target_far * len(values))
    if allowed_false_accepts <= 0:
        boundary = values[0]
        accepted_at_boundary = sum(value >= boundary for value in values)
    else:
        boundary = values[allowed_false_accepts - 1]
        accepted_at_boundary = sum(value >= boundary for value in values)
        if accepted_at_boundary <= allowed_false_accepts:
            return max(0.0, boundary)

    threshold = math.nextafter(boundary, math.inf)
    if threshold > maximum_threshold:
        raise ValueError("런타임 임계값 범위 안에서 목표 FAR을 만족할 수 없습니다.")
    return max(0.0, threshold)


def collect_track_pair_similarities(
    images: Iterable[ProbeImage], embedder: Embedder
) -> tuple[list[float], list[float]]:
    """known validation에서 동일인·타인 얼굴 쌍의 cosine 분포를 만든다."""
    samples: list[tuple[str, np.ndarray]] = []
    for image in images:
        if image.true_id is None:
            raise ValueError("얼굴 track 임계값에는 labeled known 이미지가 필요합니다.")
        embedding, _ = embedder(image.path)
        if embedding is not None:
            samples.append((image.true_id, normalize_embedding(embedding)))

    same_identity: list[float] = []
    different_identity: list[float] = []
    for (left_id, left), (right_id, right) in combinations(samples, 2):
        similarity = float(np.dot(left, right))
        destination = same_identity if left_id == right_id else different_identity
        destination.append(similarity)
    if not same_identity or not different_identity:
        raise ValueError(
            "얼굴 track 임계값에는 동일인 쌍과 서로 다른 사람 쌍이 모두 필요합니다."
        )
    return same_identity, different_identity


def classify_failure(true_id: str, predicted_id: str) -> str:
    if true_id == UNKNOWN_LABEL and predicted_id == UNKNOWN_LABEL:
        return "correct_reject"
    if true_id == UNKNOWN_LABEL and predicted_id != UNKNOWN_LABEL:
        return "false_accept"
    if true_id != UNKNOWN_LABEL and predicted_id == UNKNOWN_LABEL:
        return "false_reject"
    if true_id == predicted_id:
        return "correct_match"
    return "wrong_identity"


def evaluate_split(
    images: list[ProbeImage],
    embedder: Embedder,
    gallery: FaceGallery,
    *,
    similarity_threshold: float,
    margin_threshold: float = 0.0,
) -> list[EvalRow]:
    rows: list[EvalRow] = []
    for image in images:
        true_id = image.true_id or UNKNOWN_LABEL
        probe = score_probe(image, embedder, gallery)
        if probe is None:
            rows.append(
                EvalRow(
                    image_id=image.path.name,
                    true_id=true_id,
                    predicted_id=UNKNOWN_LABEL,
                    top1_cosine=float("nan"),
                    top2_cosine=float("nan"),
                    decision=UNKNOWN_LABEL,
                    failure_type="face_not_detected",
                    recognition_ms=0.0,
                )
            )
            continue
        accepted = (
            probe.top1_cosine >= similarity_threshold
            and probe.top1_cosine - probe.top2_cosine >= margin_threshold
        )
        predicted_id = probe.predicted_top1_id if accepted else UNKNOWN_LABEL
        rows.append(
            EvalRow(
                image_id=probe.path.name,
                true_id=true_id,
                predicted_id=predicted_id,
                top1_cosine=probe.top1_cosine,
                top2_cosine=probe.top2_cosine,
                decision=predicted_id,
                failure_type=classify_failure(true_id, predicted_id),
                recognition_ms=probe.recognition_ms,
            )
        )
    return rows


def _safe_ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def aggregate_metrics(rows: list[EvalRow]) -> AggregateMetrics:
    registered_rows = [row for row in rows if row.true_id != UNKNOWN_LABEL]
    unknown_rows = [row for row in rows if row.true_id == UNKNOWN_LABEL]
    registered_success = sum(
        1 for row in registered_rows if row.failure_type == "correct_match"
    )
    registered_false_reject = sum(
        1 for row in registered_rows if row.failure_type == "false_reject"
    )
    unknown_false_accept = sum(
        1 for row in unknown_rows if row.failure_type == "false_accept"
    )
    unknown_correct_reject = sum(
        1 for row in unknown_rows if row.failure_type == "correct_reject"
    )
    recognition_times = [
        row.recognition_ms for row in rows if row.failure_type != "face_not_detected"
    ]
    return AggregateMetrics(
        registered_success_rate=_safe_ratio(registered_success, len(registered_rows)),
        registered_false_reject_rate=_safe_ratio(
            registered_false_reject, len(registered_rows)
        ),
        unknown_false_accept_rate=_safe_ratio(unknown_false_accept, len(unknown_rows)),
        unknown_correct_reject_rate=_safe_ratio(
            unknown_correct_reject, len(unknown_rows)
        ),
        average_recognition_ms=(sum(recognition_times) / len(recognition_times))
        if recognition_times
        else 0.0,
        registered_probe_count=len(registered_rows),
        unknown_probe_count=len(unknown_rows),
    )


def write_csv(rows: list[EvalRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "image_id",
                "true_id",
                "predicted_id",
                "top1_cosine",
                "top2_cosine",
                "decision",
                "failure_type",
                "recognition_ms",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.image_id,
                    row.true_id,
                    row.predicted_id,
                    row.top1_cosine,
                    row.top2_cosine,
                    row.decision,
                    row.failure_type,
                    row.recognition_ms,
                ]
            )


def write_thresholds(
    path: Path,
    *,
    similarity_threshold: float,
    margin_threshold: float,
    track_similarity_threshold: float,
    target_far: float,
    track_target_false_association: float,
    model_name: str,
    model_version: str,
    preprocessing_version: str,
) -> None:
    """실시간 런타임이 그대로 읽을 수 있는 임계값 산출물을 쓴다."""
    if not 0.0 <= similarity_threshold <= 1.0:
        raise ValueError("similarity threshold는 0과 1 사이여야 합니다.")
    if not 0.0 <= margin_threshold <= 2.0:
        raise ValueError("margin threshold는 0과 2 사이여야 합니다.")
    if not 0.0 <= track_similarity_threshold <= 1.0:
        raise ValueError("track similarity threshold는 0과 1 사이여야 합니다.")
    if not 0.0 <= target_far <= 1.0:
        raise ValueError("target FAR은 0과 1 사이여야 합니다.")
    if not 0.0 <= track_target_false_association <= 1.0:
        raise ValueError("track false association 목표는 0과 1 사이여야 합니다.")
    if not model_name or not model_version or not preprocessing_version:
        raise ValueError("threshold metadata는 비어 있을 수 없습니다.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "similarity_threshold": similarity_threshold,
                "margin_threshold": margin_threshold,
                "track_similarity_threshold": track_similarity_threshold,
                "target_far": target_far,
                "track_target_false_association": (track_target_false_association),
                "model_name": model_name,
                "model_version": model_version,
                "preprocessing_version": preprocessing_version,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def build_gallery_from_directory(directory: Path, embedder: Embedder) -> FaceGallery:
    """MongoDB 없이 로컬 등록 사진 폴더(`<student_id>/*.jpg`)로 gallery를 만든다.

    한 사람의 여러 등록 사진 embedding을 평균해 한 학생당 벡터 하나를 쓴다.
    MongoDB에 실제 등록 데이터가 아직 없을 때(dry-run, LFW 검증 등) 쓴다 —
    공용 MongoDB에 테스트용 문서를 써 넣지 않기 위한 용도다.
    """
    entries: list[GalleryEntry] = []
    for student_dir in sorted(path for path in directory.iterdir() if path.is_dir()):
        vectors: list[np.ndarray] = []
        for image_path in sorted(_iter_images(student_dir)):
            embedding, _ = embedder(image_path)
            if embedding is not None:
                vectors.append(normalize_embedding(embedding))
        if not vectors:
            continue
        averaged = normalize_embedding(np.mean(vectors, axis=0))
        entries.append(GalleryEntry(student_dir.name, averaged))
    return FaceGallery.from_entries(entries)


def build_embedder(*, providers: list[str] | None = None) -> Embedder:
    """실제 SCRFD 검출 + `FACE_RECOGNIZER`(arcface|adaface)로 임베딩을 계산하는 embedder를 만든다.

    `cross_camera_demo.build_face_engine()`과 같은 환경변수 관례를 따르지만,
    이 하네스는 자체 threshold를 validation에서 새로 고르기 위해
    `FaceIdentityEngine`을 거치지 않고 detector/recognizer를 직접 호출한다.
    """
    import cv2
    from insightface.utils import face_align

    recognizer_kind = os.environ.get("FACE_RECOGNIZER", "arcface")
    if recognizer_kind not in ("arcface", "adaface"):
        raise ValueError(
            f"알 수 없는 FACE_RECOGNIZER={recognizer_kind!r} (arcface 또는 adaface만 지원)"
        )

    model_root = Path(__file__).resolve().parents[1] / ".models"
    detector_path = Path(
        os.environ.get("FACE_DETECTION_MODEL_PATH")
        or model_root / "scrfd/scrfd_10g_bnkps.onnx"
    ).resolve()
    recognizer_path = Path(os.environ["FACE_RECOGNITION_MODEL_PATH"]).resolve()
    for path in (detector_path, recognizer_path):
        if not path.is_file():
            raise FileNotFoundError(path)

    active_providers = providers or ["CUDAExecutionProvider", "CPUExecutionProvider"]
    import onnxruntime as ort

    if "CUDAExecutionProvider" not in ort.get_available_providers():
        active_providers = ["CPUExecutionProvider"]

    from insightface.model_zoo import get_model

    detector = get_model(str(detector_path), providers=active_providers)
    input_size = int(os.environ.get("FACE_DETECTION_INPUT_SIZE", "1280"))
    detector.prepare(
        ctx_id=0 if active_providers[0].startswith("CUDA") else -1,
        input_size=(input_size, input_size),
        det_thresh=float(os.environ.get("FACE_DETECTION_THRESHOLD", "0.6")),
    )

    if recognizer_kind == "adaface":
        # InsightFace의 get_model()은 ONNX metadata로 모델 종류를 자동판별한다.
        # InsightFace가 만들지 않은 AdaFace ONNX를 넣으면 잘못 판별될 수 있어
        # 여기서는 직접 onnxruntime 세션을 여는 전용 어댑터를 쓴다.
        from deeplearning.training.adaface_recognizer import AdaFaceOnnxRecognizer

        recognizer = AdaFaceOnnxRecognizer(recognizer_path, providers=active_providers)
    else:
        recognizer = get_model(str(recognizer_path), providers=active_providers)
    recognizer.prepare(ctx_id=0 if active_providers[0].startswith("CUDA") else -1)

    def embedder(image_path: Path) -> tuple[np.ndarray | None, float]:
        image_bgr = cv2.imread(str(image_path))
        if image_bgr is None:
            return None, 0.0
        detections, keypoints = detector.detect(image_bgr, max_num=0)
        if keypoints is None or len(keypoints) == 0:
            return None, 0.0
        best_index = int(np.argmax(detections[:, 4]))
        aligned = face_align.norm_crop(
            image_bgr, landmark=keypoints[best_index], image_size=112
        )
        started = time.perf_counter()
        raw_embedding = recognizer.get_feat(aligned)
        recognition_ms = (time.perf_counter() - started) * 1000.0
        return normalize_embedding(raw_embedding), recognition_ms

    return embedder


def build_mongo_gallery(*, expected_model_name: str | None = None) -> FaceGallery:
    """런타임과 같은 필터로 MongoDB 등록 학생 gallery를 읽는다.

    ArcFace/AdaFace는 임베딩 공간이 다르므로 gallery 조회 시 반드시 모델별로
    분리한다 — `expected_model_name`을 안 주면 `FACE_RECOGNIZER`(기본 arcface)를 쓴다.
    """
    if expected_model_name is None:
        expected_model_name = os.environ.get(
            "FACE_EMBEDDING_MODEL_NAME", os.environ.get("FACE_RECOGNIZER", "arcface")
        )
    expected_model_version = os.environ.get(
        "FACE_EMBEDDING_MODEL_VERSION",
        "insightface-buffalo_l-w600k_r50-v0.7",
    )
    expected_preprocessing_version = os.environ.get(
        "FACE_EMBEDDING_PREPROCESSING_VERSION",
        "insightface-norm-crop-112-v1",
    )

    mongodb_uri = os.environ.get("MONGODB_URI") or os.environ.get("DATABASE_URL", "")
    mongodb_database = os.environ.get("MONGODB_DATABASE") or os.environ.get(
        "DATABASE_NAME", ""
    )
    if not mongodb_uri.strip():
        raise RuntimeError("MongoDB gallery에는 MONGODB_URI가 필요합니다.")
    if not mongodb_database.strip():
        raise RuntimeError("MongoDB gallery에는 MONGODB_DATABASE가 필요합니다.")
    collection_name = os.environ.get("FACE_EMBEDDING_COLLECTION", "face_embeddings")

    loader = MongoFaceGalleryLoader(
        database_url=mongodb_uri,
        database_name=mongodb_database,
        collection_name=collection_name,
        expected_metadata=FaceModelMetadata(
            expected_model_name,
            expected_model_version,
            expected_preprocessing_version,
        ),
        timeout_seconds=10.0,
    )
    try:
        snapshot = loader.load()
    except FaceGalleryUnavailable as error:
        # 학생 ID·벡터·저장소 세부 오류는 평가 로그로 내보내지 않는다.
        raise RuntimeError("MongoDB 등록 얼굴 gallery를 사용할 수 없습니다.") from error
    return FaceGallery.from_entries(snapshot.entries)


def build_embedder_and_gallery(
    *, providers: list[str] | None = None
) -> tuple[Embedder, FaceGallery]:
    """`build_embedder()` + `build_mongo_gallery()`를 함께 만든다 (기존 호출부 호환용)."""
    embedder = build_embedder(providers=providers)
    gallery = build_mongo_gallery()
    return embedder, gallery


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).with_name(".env"))
    known_validation_dir = Path(os.environ["FACE_EVAL_KNOWN_VALIDATION_DIR"])
    unknown_validation_dir = Path(os.environ["FACE_EVAL_UNKNOWN_VALIDATION_DIR"])
    known_test_dir = Path(os.environ["FACE_EVAL_KNOWN_TEST_DIR"])
    unknown_test_dir = Path(os.environ["FACE_EVAL_UNKNOWN_TEST_DIR"])
    target_far = float(os.environ.get("FACE_EVAL_TARGET_FAR", "0.001"))
    track_target_false_association = float(
        os.environ.get("FACE_EVAL_TRACK_TARGET_FALSE_ASSOCIATION", "0.001")
    )
    output_csv = Path(
        os.environ.get("FACE_EVAL_OUTPUT_CSV")
        or Path(__file__).resolve().parents[1]
        / "training/runs/face_identification/eval.csv"
    )
    threshold_output = Path(
        os.environ.get("FACE_EVAL_THRESHOLD_OUTPUT")
        or output_csv.with_name("thresholds.json")
    )

    embedder = build_embedder()
    gallery_source = os.environ.get("FACE_EVAL_GALLERY_SOURCE", "mongodb")
    if gallery_source == "directory":
        # MongoDB에 실제 등록 데이터가 없을 때(dry-run) 로컬 등록 사진 폴더로 gallery를 만든다.
        gallery = build_gallery_from_directory(
            Path(os.environ["FACE_EVAL_GALLERY_DIR"]), embedder
        )
    elif gallery_source == "mongodb":
        gallery = build_mongo_gallery()
    else:
        raise ValueError(
            f"알 수 없는 FACE_EVAL_GALLERY_SOURCE={gallery_source!r} (mongodb 또는 directory만 지원)"
        )

    known_validation = load_split(known_validation_dir, labeled=True)
    unknown_validation = load_split(unknown_validation_dir, labeled=False)
    known_test = load_split(known_test_dir, labeled=True)
    unknown_test = load_split(unknown_test_dir, labeled=False)
    validate_evaluation_inputs(
        gallery,
        known_validation=known_validation,
        unknown_validation=unknown_validation,
        known_test=known_test,
        unknown_test=unknown_test,
    )
    validation_scores = [
        score
        for score in (
            score_probe(image, embedder, gallery)
            for image in known_validation + unknown_validation
        )
        if score is not None
    ]
    unknown_validation_scores = [
        score for score in validation_scores if score.true_id is None
    ]
    similarity_threshold = select_threshold_for_far(
        (score.top1_cosine for score in unknown_validation_scores),
        target_far=target_far,
    )
    wrong_known_validation_scores = [
        score
        for score in validation_scores
        if score.true_id is not None and score.predicted_top1_id != score.true_id
    ]
    margin_threshold = (
        select_threshold_for_far(
            (
                score.top1_cosine - score.top2_cosine
                for score in wrong_known_validation_scores
            ),
            target_far=target_far,
            maximum_threshold=2.0,
        )
        if wrong_known_validation_scores
        else 0.0
    )
    same_identity_similarities, different_identity_similarities = (
        collect_track_pair_similarities(known_validation, embedder)
    )
    track_similarity_threshold = select_threshold_for_far(
        different_identity_similarities,
        target_far=track_target_false_association,
    )
    track_same_identity_accept_rate = _safe_ratio(
        sum(
            similarity >= track_similarity_threshold
            for similarity in same_identity_similarities
        ),
        len(same_identity_similarities),
    )

    test_images = known_test + unknown_test
    rows = evaluate_split(
        test_images,
        embedder,
        gallery,
        similarity_threshold=similarity_threshold,
        margin_threshold=margin_threshold,
    )
    write_csv(rows, output_csv)
    model_name = os.environ.get(
        "FACE_EMBEDDING_MODEL_NAME", os.environ.get("FACE_RECOGNIZER", "arcface")
    )
    model_version = os.environ.get(
        "FACE_EMBEDDING_MODEL_VERSION",
        "insightface-buffalo_l-w600k_r50-v0.7",
    )
    preprocessing_version = os.environ.get(
        "FACE_EMBEDDING_PREPROCESSING_VERSION",
        "insightface-norm-crop-112-v1",
    )
    write_thresholds(
        threshold_output,
        similarity_threshold=similarity_threshold,
        margin_threshold=margin_threshold,
        track_similarity_threshold=track_similarity_threshold,
        target_far=target_far,
        track_target_false_association=track_target_false_association,
        model_name=model_name,
        model_version=model_version,
        preprocessing_version=preprocessing_version,
    )
    metrics = aggregate_metrics(rows)

    print(f"selected_similarity_threshold={similarity_threshold:.4f}")
    print(f"selected_margin_threshold={margin_threshold:.4f}")
    print(f"selected_track_similarity_threshold={track_similarity_threshold:.4f}")
    print(f"track_same_identity_accept_rate={track_same_identity_accept_rate:.4f}")
    print(f"registered_success_rate={metrics.registered_success_rate:.4f}")
    print(f"registered_false_reject_rate={metrics.registered_false_reject_rate:.4f}")
    print(f"unknown_false_accept_rate={metrics.unknown_false_accept_rate:.4f}")
    print(f"unknown_correct_reject_rate={metrics.unknown_correct_reject_rate:.4f}")
    print(f"average_recognition_ms={metrics.average_recognition_ms:.4f}")
    print(f"registered_probe_count={metrics.registered_probe_count}")
    print(f"unknown_probe_count={metrics.unknown_probe_count}")
    print(f"csv_written_to={output_csv}")
    print(f"thresholds_written_to={threshold_output}")


if __name__ == "__main__":
    main()
