from __future__ import annotations

import hashlib
import math
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from .core import (
    SAFE_ID_PATTERN,
    CandidateBox,
    Settings,
    frame_id_from_record,
    read_json,
    read_jsonl,
    sha256_file,
    utc_now_iso,
    verified_frame_image_path,
    write_json,
    write_jsonl,
)
from .errors import AutoLabelingError
from .yolo import YoloBox, parse_yolo_file, validate_yolo_box, write_yolo_file


@dataclass(frozen=True)
class ModelInfo:
    model_path: str
    model_file_name: str
    model_sha256: str
    model_runtime: str
    model_runtime_version: str
    device: str


class CandidatePredictor(Protocol):
    def predict(self, image_path: Path) -> list[CandidateBox]: ...


class UltralyticsPredictor:
    def __init__(
        self, model_path: Path, *, confidence_threshold: float, device: str
    ) -> None:
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise AutoLabelingError("ultralytics가 설치되어 있지 않습니다.") from exc
        try:
            self._model = YOLO(str(model_path))
        except Exception as exc:  # 외부 모델 로더가 여러 런타임 예외를 사용한다.
            raise AutoLabelingError("모델 가중치를 로드할 수 없습니다.") from exc
        self._confidence_threshold = confidence_threshold
        self._device = device
        self._person_class_id = _find_person_class_id(self._model.names)

    def predict(self, image_path: Path) -> list[CandidateBox]:
        try:
            results = self._model.predict(
                source=str(image_path),
                conf=self._confidence_threshold,
                device=self._device,
                verbose=False,
            )
        except Exception as exc:  # 외부 추론 런타임 오류를 도구 계약으로 변환한다.
            raise AutoLabelingError("후보 bbox 추론에 실패했습니다.") from exc
        result_list = list(results)
        if len(result_list) != 1:
            raise AutoLabelingError("이미지 한 장의 모델 결과가 하나가 아닙니다.")
        result: Any = result_list[0]
        image_height, image_width = result.orig_shape
        candidates: list[CandidateBox] = []
        if result.boxes is None:
            return candidates
        xyxy_values = result.boxes.xyxy.cpu().tolist()
        confidence_values = result.boxes.conf.cpu().tolist()
        class_values = result.boxes.cls.cpu().tolist()
        for xyxy, confidence, class_value in zip(
            xyxy_values, confidence_values, class_values, strict=True
        ):
            if int(class_value) != self._person_class_id:
                continue
            left, top, right, bottom = (float(value) for value in xyxy)
            candidate = candidate_from_pixels(
                (left, top, right, bottom),
                float(confidence),
                image_width=image_width,
                image_height=image_height,
            )
            if candidate is not None:
                candidates.append(candidate)
        return candidates


def _find_person_class_id(names: object) -> int:
    if isinstance(names, dict):
        matches = [
            int(class_id) for class_id, name in names.items() if name == "person"
        ]
    elif isinstance(names, list):
        matches = [index for index, name in enumerate(names) if name == "person"]
    else:
        matches = []
    if len(matches) != 1:
        raise AutoLabelingError(
            "모델 클래스 목록에서 person을 하나만 찾을 수 있어야 합니다."
        )
    return matches[0]


def candidate_from_pixels(
    bbox_xyxy_pixels: tuple[float, float, float, float],
    confidence: float,
    *,
    image_width: int,
    image_height: int,
) -> CandidateBox | None:
    if image_width <= 0 or image_height <= 0:
        raise AutoLabelingError("모델 결과의 이미지 크기가 올바르지 않습니다.")
    left, top, right, bottom = bbox_xyxy_pixels
    if not all(
        math.isfinite(value) for value in (left, top, right, bottom, confidence)
    ):
        raise AutoLabelingError("모델 후보 bbox와 confidence는 유한수여야 합니다.")
    if not 0 <= confidence <= 1:
        raise AutoLabelingError("모델 후보 confidence는 0~1이어야 합니다.")
    left = min(max(left, 0.0), float(image_width))
    top = min(max(top, 0.0), float(image_height))
    right = min(max(right, 0.0), float(image_width))
    bottom = min(max(bottom, 0.0), float(image_height))
    width = right - left
    height = bottom - top
    if width <= 0 or height <= 0:
        return None
    center_x = (left + right) / 2 / image_width
    center_y = (top + bottom) / 2 / image_height
    normalized_width = width / image_width
    normalized_height = height / image_height
    return CandidateBox(
        class_id=0,
        class_name="person",
        confidence=confidence,
        bbox_xyxy_pixels=(left, top, right, bottom),
        bbox_yolo=(center_x, center_y, normalized_width, normalized_height),
    )


def run_prelabel(
    run_dir: Path,
    model_path: Path,
    settings: Settings,
    *,
    device: str,
) -> Path:
    try:
        resolved_model_path = model_path.resolve(strict=True)
    except OSError as exc:
        raise AutoLabelingError("모델 가중치 파일을 찾을 수 없습니다.") from exc
    if not resolved_model_path.is_file():
        raise AutoLabelingError("모델 가중치 경로는 파일이어야 합니다.")
    if resolved_model_path.name != settings.pilot_model_file_name:
        raise AutoLabelingError(
            f"V1 파일럿 모델 파일 이름은 {settings.pilot_model_file_name}이어야 합니다."
        )
    try:
        import ultralytics
    except ImportError as exc:
        raise AutoLabelingError("ultralytics가 설치되어 있지 않습니다.") from exc
    model_info = ModelInfo(
        model_path=str(resolved_model_path),
        model_file_name=resolved_model_path.name,
        model_sha256=sha256_file(resolved_model_path),
        model_runtime="ultralytics",
        model_runtime_version=ultralytics.__version__,
        device=device,
    )
    predictor = UltralyticsPredictor(
        resolved_model_path,
        confidence_threshold=settings.candidate_confidence_threshold,
        device=device,
    )
    return generate_candidate_labels(run_dir, predictor, model_info, settings)


def generate_candidate_labels(
    run_dir: Path,
    predictor: CandidatePredictor,
    model_info: ModelInfo,
    settings: Settings,
) -> Path:
    run_dir = run_dir.resolve(strict=True)
    run_manifest = read_json(run_dir / "run.json")
    if not isinstance(run_manifest, dict):
        raise AutoLabelingError("run.json 형식이 올바르지 않습니다.")
    prelabel_manifest_path = run_dir / "prelabel.json"
    candidate_dir = run_dir / "candidate-labels"
    if prelabel_manifest_path.exists():
        _verify_existing_prelabel(
            run_dir, prelabel_manifest_path, model_info, settings, run_manifest
        )
        return candidate_dir
    if candidate_dir.exists() or (run_dir / "predictions.jsonl").exists():
        raise AutoLabelingError("불완전한 후보 라벨 산출물이 있습니다.")

    frames = read_jsonl(run_dir / "frames.jsonl")
    if len(frames) != run_manifest.get("frame_count"):
        raise AutoLabelingError("프레임 manifest 수가 run.json과 다릅니다.")
    prediction_rows: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix=".prelabel-", dir=run_dir) as temporary:
        temporary_dir = Path(temporary)
        temporary_labels = temporary_dir / "candidate-labels"
        temporary_labels.mkdir()
        for frame in frames:
            frame_id = frame_id_from_record(frame)
            image_path = verified_frame_image_path(run_dir, frame)
            candidates = [
                candidate
                for candidate in predictor.predict(image_path)
                if candidate.confidence >= settings.candidate_confidence_threshold
            ]
            for candidate in candidates:
                _validate_candidate(candidate)
            candidates.sort(key=lambda item: (-item.confidence, item.bbox_yolo))
            yolo_boxes = [
                YoloBox(candidate.class_id, *candidate.bbox_yolo)
                for candidate in candidates
            ]
            write_yolo_file(temporary_labels / f"{frame_id}.txt", yolo_boxes)
            prediction_rows.append(
                {
                    "frame_id": frame_id,
                    "source_id": frame.get("source_id"),
                    "camera_id": frame.get("camera_id"),
                    "session_id": frame.get("session_id"),
                    "timestamp_ms": frame.get("timestamp_ms"),
                    "candidates": [candidate.to_dict() for candidate in candidates],
                }
            )
        predictions_path = temporary_dir / "predictions.jsonl"
        write_jsonl(predictions_path, prediction_rows)
        temporary_labels.replace(candidate_dir)
        predictions_path.replace(run_dir / "predictions.jsonl")
    frame_ids = [frame_id_from_record(frame) for frame in frames]
    write_json(
        prelabel_manifest_path,
        {
            "schema_version": 1,
            "run_id": run_manifest.get("run_id"),
            "frame_count": len(frames),
            "candidate_confidence_threshold": settings.candidate_confidence_threshold,
            "model": asdict(model_info),
            "predictions_sha256": sha256_file(run_dir / "predictions.jsonl"),
            "candidate_labels_fingerprint": candidate_labels_fingerprint(
                candidate_dir, frame_ids
            ),
            "created_at": utc_now_iso(),
        },
    )
    return candidate_dir


def _verify_existing_prelabel(
    run_dir: Path,
    manifest_path: Path,
    model_info: ModelInfo,
    settings: Settings,
    run_manifest: dict[str, object],
) -> None:
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise AutoLabelingError("prelabel.json 형식이 올바르지 않습니다.")
    if manifest.get("model") != asdict(model_info):
        raise AutoLabelingError("같은 run에 다른 모델 후보 라벨이 이미 있습니다.")
    if (
        manifest.get("candidate_confidence_threshold")
        != settings.candidate_confidence_threshold
    ):
        raise AutoLabelingError("같은 run에 다른 confidence 후보 라벨이 이미 있습니다.")
    if manifest.get("frame_count") != run_manifest.get("frame_count"):
        raise AutoLabelingError("기존 후보 라벨 수가 run.json과 다릅니다.")
    verify_prelabel_artifacts(run_dir, manifest=manifest)


def verify_prelabel_artifacts(
    run_dir: Path,
    *,
    manifest: dict[str, object] | None = None,
    frames: list[dict[str, Any]] | None = None,
) -> dict[str, object]:
    prelabel_manifest = manifest or _require_manifest(
        read_json(run_dir / "prelabel.json")
    )
    frame_rows = frames or read_jsonl(run_dir / "frames.jsonl")
    frame_ids = [frame_id_from_record(frame) for frame in frame_rows]
    if len(frame_ids) != len(set(frame_ids)):
        raise AutoLabelingError("frames.jsonl에 중복 frame_id가 있습니다.")
    if prelabel_manifest.get("frame_count") != len(frame_ids):
        raise AutoLabelingError("후보 라벨 프레임 수가 frames.jsonl과 다릅니다.")
    for frame in frame_rows:
        verified_frame_image_path(run_dir, frame)

    predictions_path = run_dir / "predictions.jsonl"
    expected_predictions_hash = prelabel_manifest.get("predictions_sha256")
    if expected_predictions_hash != sha256_file(predictions_path):
        raise AutoLabelingError("후보 생성 뒤 predictions.jsonl이 변경됐습니다.")
    prediction_rows = read_jsonl(predictions_path)
    prediction_ids = [_frame_id(row) for row in prediction_rows]
    if len(prediction_ids) != len(set(prediction_ids)) or set(prediction_ids) != set(
        frame_ids
    ):
        raise AutoLabelingError("예측과 프레임의 ID 집합이 다릅니다.")

    actual_fingerprint = candidate_labels_fingerprint(
        run_dir / "candidate-labels", frame_ids
    )
    if prelabel_manifest.get("candidate_labels_fingerprint") != actual_fingerprint:
        raise AutoLabelingError("후보 생성 뒤 라벨 파일이 변경됐습니다.")
    return prelabel_manifest


def candidate_labels_fingerprint(candidate_dir: Path, frame_ids: Iterable[str]) -> str:
    expected_ids = set(frame_ids)
    actual_ids = {path.stem for path in candidate_dir.glob("*.txt")}
    if not candidate_dir.is_dir() or actual_ids != expected_ids:
        raise AutoLabelingError("후보 라벨 파일 집합이 프레임과 다릅니다.")
    digest = hashlib.sha256()
    for frame_id in sorted(expected_ids):
        label_path = candidate_dir / f"{frame_id}.txt"
        if label_path.is_symlink():
            raise AutoLabelingError("후보 라벨에는 심볼릭 링크를 사용할 수 없습니다.")
        parse_yolo_file(label_path)
        digest.update(f"{frame_id}:{sha256_file(label_path)}\n".encode())
    return digest.hexdigest()


def _validate_candidate(candidate: CandidateBox) -> None:
    if candidate.class_id != 0 or candidate.class_name != "person":
        raise AutoLabelingError("모델 후보 클래스는 0: person이어야 합니다.")
    if not math.isfinite(candidate.confidence) or not 0 <= candidate.confidence <= 1:
        raise AutoLabelingError("모델 후보 confidence는 0~1 유한수여야 합니다.")
    if not all(math.isfinite(value) for value in candidate.bbox_xyxy_pixels):
        raise AutoLabelingError("모델 후보 픽셀 bbox는 유한수여야 합니다.")
    validate_box = YoloBox(candidate.class_id, *candidate.bbox_yolo)
    validate_yolo_box(validate_box, "predictions.jsonl", 1)


def _require_manifest(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise AutoLabelingError("prelabel.json 형식이 올바르지 않습니다.")
    return value


def _frame_id(frame: dict[str, object]) -> str:
    value = frame.get("frame_id")
    if not isinstance(value, str) or not SAFE_ID_PATTERN.fullmatch(value):
        raise AutoLabelingError("predictions.jsonl에 올바른 frame_id가 없습니다.")
    return value
