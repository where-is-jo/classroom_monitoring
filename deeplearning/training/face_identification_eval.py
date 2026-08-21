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
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

import numpy as np

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
    images: list[ProbeImage] = []
    if labeled:
        for student_dir in sorted(path for path in root.iterdir() if path.is_dir()):
            for image_path in sorted(_iter_images(student_dir)):
                images.append(ProbeImage(image_path, student_dir.name))
    else:
        for image_path in sorted(_iter_images(root)):
            images.append(ProbeImage(image_path, None))
    return images


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
) -> float:
    """미등록(unknown) validation의 top-1 cosine 분포에서 목표 FAR 이하를 만족하는
    가장 낮은(관대한) similarity threshold를 고른다.

    동점(tie)이 있으면 실제 FAR이 목표보다 살짝 높아질 수 있다 — 이 경우 목표
    허용치를 하나 줄여 다시 계산해야 한다는 뜻이므로 호출부에서 결과 FAR을
    반드시 재확인한다.
    """
    values = sorted(unknown_top1_cosines, reverse=True)
    if not values:
        raise ValueError("unknown validation 점수가 비어 있습니다.")
    allowed_false_accepts = math.floor(target_far * len(values))
    if allowed_false_accepts <= 0:
        return values[0] + 1e-6
    return values[allowed_false_accepts - 1]


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
        accepted = probe.top1_cosine >= similarity_threshold
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
    registered_success = sum(1 for row in registered_rows if row.failure_type == "correct_match")
    registered_false_reject = sum(1 for row in registered_rows if row.failure_type == "false_reject")
    unknown_false_accept = sum(1 for row in unknown_rows if row.failure_type == "false_accept")
    unknown_correct_reject = sum(1 for row in unknown_rows if row.failure_type == "correct_reject")
    recognition_times = [row.recognition_ms for row in rows if row.failure_type != "face_not_detected"]
    return AggregateMetrics(
        registered_success_rate=_safe_ratio(registered_success, len(registered_rows)),
        registered_false_reject_rate=_safe_ratio(registered_false_reject, len(registered_rows)),
        unknown_false_accept_rate=_safe_ratio(unknown_false_accept, len(unknown_rows)),
        unknown_correct_reject_rate=_safe_ratio(unknown_correct_reject, len(unknown_rows)),
        average_recognition_ms=(sum(recognition_times) / len(recognition_times)) if recognition_times else 0.0,
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


def build_embedder_and_gallery(*, providers: list[str] | None = None) -> tuple[Embedder, FaceGallery]:
    """실제 SCRFD 검출 + 지정된 인식 모델로 임베딩을 계산하는 embedder와 Mongo gallery를 만든다.

    `cross_camera_demo.build_face_engine()`과 같은 환경변수 관례를 따르지만,
    이 하네스는 자체 threshold를 validation에서 새로 고르기 위해
    `FaceIdentityEngine`을 거치지 않고 detector/recognizer를 직접 호출한다.
    ArcFace와 AdaFace를 같은 조건에서 비교하려면 `FACE_RECOGNITION_MODEL_PATH`,
    `FACE_EMBEDDING_MODEL_NAME`(gallery 조회용 model_name 필터), `FACE_EMBEDDING_COLLECTION`을
    모델별로 바꿔서 실행한다.
    """
    import cv2
    from insightface.model_zoo import get_model
    from insightface.utils import face_align
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError

    model_root = Path(__file__).resolve().parents[1] / ".models"
    detector_path = Path(
        os.environ.get("FACE_DETECTION_MODEL_PATH") or model_root / "scrfd/scrfd_10g_bnkps.onnx"
    ).resolve()
    recognizer_path = Path(os.environ["FACE_RECOGNITION_MODEL_PATH"]).resolve()
    for path in (detector_path, recognizer_path):
        if not path.is_file():
            raise FileNotFoundError(path)

    mongodb_uri = os.environ.get("MONGODB_URI") or os.environ.get("DATABASE_URL", "")
    mongodb_database = os.environ.get("MONGODB_DATABASE") or os.environ.get("DATABASE_NAME", "")
    collection_name = os.environ.get("FACE_EMBEDDING_COLLECTION", "face_embeddings")
    expected_model_name = os.environ.get("FACE_EMBEDDING_MODEL_NAME", "arcface")

    entries: list[GalleryEntry] = []
    client = MongoClient(mongodb_uri, serverSelectionTimeoutMS=10_000, connectTimeoutMS=10_000)
    try:
        client.admin.command("ping")
        projection = {"_id": 0, "student_id": 1, "vector": 1, "dimension": 1, "normalized": 1, "model_name": 1}
        for document in client[mongodb_database][collection_name].find({}, projection):
            student_id = document.get("student_id")
            if (
                not isinstance(student_id, str)
                or not student_id
                or document.get("dimension") != 512
                or document.get("normalized") is not True
                or document.get("model_name") != expected_model_name
            ):
                raise RuntimeError(f"{student_id!r} 얼굴 벡터가 {expected_model_name} gallery 조건과 다릅니다.")
            entries.append(GalleryEntry(student_id, np.asarray(document.get("vector"), dtype=np.float32)))
    except PyMongoError as exc:
        raise RuntimeError("MongoDB 연결/조회에 실패했습니다.") from exc
    finally:
        client.close()
    if not entries:
        raise RuntimeError(f"{expected_model_name} 등록 얼굴 gallery가 비어 있습니다.")
    gallery = FaceGallery.from_entries(entries)

    active_providers = providers or ["CUDAExecutionProvider", "CPUExecutionProvider"]
    import onnxruntime as ort

    if "CUDAExecutionProvider" not in ort.get_available_providers():
        active_providers = ["CPUExecutionProvider"]
    detector = get_model(str(detector_path), providers=active_providers)
    input_size = int(os.environ.get("FACE_DETECTION_INPUT_SIZE", "1280"))
    detector.prepare(
        ctx_id=0 if active_providers[0].startswith("CUDA") else -1,
        input_size=(input_size, input_size),
        det_thresh=float(os.environ.get("FACE_DETECTION_THRESHOLD", "0.6")),
    )
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
        aligned = face_align.norm_crop(image_bgr, landmark=keypoints[best_index], image_size=112)
        started = time.perf_counter()
        raw_embedding = recognizer.get_feat(aligned)
        recognition_ms = (time.perf_counter() - started) * 1000.0
        return normalize_embedding(raw_embedding), recognition_ms

    return embedder, gallery


def main() -> None:
    known_validation_dir = Path(os.environ["FACE_EVAL_KNOWN_VALIDATION_DIR"])
    unknown_validation_dir = Path(os.environ["FACE_EVAL_UNKNOWN_VALIDATION_DIR"])
    known_test_dir = Path(os.environ["FACE_EVAL_KNOWN_TEST_DIR"])
    unknown_test_dir = Path(os.environ["FACE_EVAL_UNKNOWN_TEST_DIR"])
    target_far = float(os.environ.get("FACE_EVAL_TARGET_FAR", "0.001"))
    output_csv = Path(
        os.environ.get("FACE_EVAL_OUTPUT_CSV")
        or Path(__file__).resolve().parents[1] / "training/runs/face_identification/eval.csv"
    )

    embedder, gallery = build_embedder_and_gallery()

    known_validation = load_split(known_validation_dir, labeled=True)
    unknown_validation = load_split(unknown_validation_dir, labeled=False)
    validation_scores = [
        score for score in (score_probe(image, embedder, gallery) for image in known_validation + unknown_validation)
        if score is not None
    ]
    unknown_validation_scores = [score for score in validation_scores if score.true_id is None]
    threshold = select_threshold_for_far(
        (score.top1_cosine for score in unknown_validation_scores),
        target_far=target_far,
    )

    test_images = load_split(known_test_dir, labeled=True) + load_split(unknown_test_dir, labeled=False)
    rows = evaluate_split(test_images, embedder, gallery, similarity_threshold=threshold)
    write_csv(rows, output_csv)
    metrics = aggregate_metrics(rows)

    print(f"selected_similarity_threshold={threshold:.4f}")
    print(f"registered_success_rate={metrics.registered_success_rate:.4f}")
    print(f"registered_false_reject_rate={metrics.registered_false_reject_rate:.4f}")
    print(f"unknown_false_accept_rate={metrics.unknown_false_accept_rate:.4f}")
    print(f"unknown_correct_reject_rate={metrics.unknown_correct_reject_rate:.4f}")
    print(f"average_recognition_ms={metrics.average_recognition_ms:.4f}")
    print(f"registered_probe_count={metrics.registered_probe_count}")
    print(f"unknown_probe_count={metrics.unknown_probe_count}")
    print(f"csv_written_to={output_csv}")


if __name__ == "__main__":
    main()
