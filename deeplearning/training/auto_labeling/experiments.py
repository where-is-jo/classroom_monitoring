from __future__ import annotations

import importlib
import importlib.metadata
import json
import math
import os
import platform
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .core import sha256_file, utc_now_iso, write_json
from .errors import AutoLabelingError
from .evaluation import verify_frozen_evaluation_set
from .privacy import validate_privacy_export
from .yolo import YoloBox, iou, parse_yolo_file

DEFAULT_F1_THRESHOLDS = tuple(value / 20 for value in range(1, 20))
yaml: Any = importlib.import_module("yaml")


@dataclass(frozen=True)
class TrainingConfig:
    epochs: int = 100
    image_size: int = 640
    batch: int = 16
    device: str = "0"
    seed: int = 42
    workers: int = 2
    patience: int = 20
    confidence: float = 0.25


def train_person_detector(
    model_name: str,
    deidentified_dataset_dir: Path,
    output_root: Path,
    *,
    experiment_name: str,
    config: TrainingConfig | None = None,
    resume: bool = False,
) -> Path:
    active = config or TrainingConfig()
    _validate_training_config(active)
    model_file_name = Path(model_name).name
    if model_file_name not in {"yolov8n.pt", "yolo11n.pt"}:
        raise AutoLabelingError("비교 모델은 yolov8n.pt 또는 yolo11n.pt만 허용합니다.")
    model_source = Path(model_name)
    source_model_sha256: str | None = None
    if model_name != model_file_name:
        try:
            source_model_sha256 = sha256_file(model_source.resolve(strict=True))
        except OSError as exc:
            raise AutoLabelingError("로컬 기준 모델 파일을 찾을 수 없습니다.") from exc
    if not experiment_name or any(char in experiment_name for char in "\\/:"):
        raise AutoLabelingError("experiment_name 형식이 올바르지 않습니다.")
    dataset_root = deidentified_dataset_dir.resolve(strict=True)
    privacy_report = validate_privacy_export(dataset_root)
    if privacy_report.get("training_compatible") is not True:
        raise AutoLabelingError(
            "정답 bbox에서 만든 픽셀화 데이터는 학습할 수 없습니다. "
            "라벨과 독립적이며 실제 추론에도 동일하게 적용 가능한 "
            "전처리로 새 데이터 버전을 만드세요."
        )
    data_yaml = dataset_root / "data.yaml"
    if not data_yaml.is_file():
        raise AutoLabelingError("비식별화 데이터셋의 data.yaml이 없습니다.")
    output = output_root.resolve()
    output.mkdir(parents=True, exist_ok=True)
    experiment_dir = output / experiment_name
    resume_checkpoint: Path | None = None
    resume_checkpoint_sha256: str | None = None
    if resume:
        if not experiment_dir.is_dir():
            raise AutoLabelingError("재개할 실험 출력 디렉터리가 없습니다.")
        if (experiment_dir / "training_receipt.json").exists():
            raise AutoLabelingError("이미 완료된 실험은 재개할 수 없습니다.")
        resume_checkpoint = experiment_dir / "weights" / "last.pt"
        if not resume_checkpoint.is_file():
            raise AutoLabelingError("재개할 last.pt 체크포인트가 없습니다.")
        resume_checkpoint_sha256 = sha256_file(resume_checkpoint)
    elif experiment_dir.exists():
        raise AutoLabelingError("동일한 실험 이름의 출력이 이미 있습니다.")

    ultralytics_config_dir = output / ".ultralytics"
    YOLO = _load_yolo_with_isolated_config(ultralytics_config_dir)
    model = YOLO(str(resume_checkpoint) if resume_checkpoint else model_name)
    if source_model_sha256 is None and not resume_checkpoint:
        checkpoint_path = getattr(model, "ckpt_path", None)
        if isinstance(checkpoint_path, (str, Path)):
            candidate = Path(checkpoint_path)
            if candidate.is_file():
                source_model_sha256 = sha256_file(candidate.resolve())
    started = time.perf_counter()
    if resume_checkpoint:
        result = model.train(resume=True)
    else:
        # Ultralytics resolves ``path: .`` against the process working directory
        # in some releases instead of the directory containing data.yaml.  Colab
        # normally runs from /content, so pass a short-lived YAML with an absolute
        # dataset root while leaving the validated export immutable.
        with tempfile.TemporaryDirectory(
            prefix=".training-data-",
            dir=output,
        ) as runtime_dir:
            runtime_data_yaml = Path(runtime_dir) / "data.yaml"
            data_config = yaml.safe_load(data_yaml.read_text(encoding="utf-8"))
            if not isinstance(data_config, dict):
                raise AutoLabelingError("data.yaml은 YAML 객체여야 합니다.")
            data_config["path"] = str(dataset_root)
            runtime_data_yaml.write_text(
                yaml.safe_dump(
                    data_config,
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            result = model.train(
                data=str(runtime_data_yaml),
                epochs=active.epochs,
                imgsz=active.image_size,
                batch=active.batch,
                device=active.device,
                seed=active.seed,
                workers=active.workers,
                patience=active.patience,
                project=str(output),
                name=experiment_name,
                exist_ok=False,
                classes=[0],
                deterministic=True,
                plots=True,
            )
    duration = time.perf_counter() - started
    save_dir = Path(str(result.save_dir)).resolve(strict=True)
    best_weight = save_dir / "weights" / "best.pt"
    last_weight = save_dir / "weights" / "last.pt"
    if not best_weight.is_file() or not last_weight.is_file():
        raise AutoLabelingError("학습 결과 가중치가 생성되지 않았습니다.")
    receipt_path = save_dir / "training_receipt.json"
    write_json(
        receipt_path,
        {
            "schema_version": 1,
            "experiment_name": experiment_name,
            "model_name": model_name,
            "model_file_name": model_file_name,
            "model_role": (
                "baseline" if model_file_name == "yolov8n.pt" else "comparison"
            ),
            "source_model_sha256": source_model_sha256,
            "runtime": _training_runtime_metadata(),
            "config": asdict(active),
            "resumed": resume,
            "resumed_from_sha256": resume_checkpoint_sha256,
            "dataset": privacy_report,
            "data_yaml_sha256": sha256_file(data_yaml),
            "runtime_data_yaml_path_mode": "absolute-dataset-root",
            "ultralytics_config_dir": str(ultralytics_config_dir),
            "training_seconds": round(duration, 3),
            "best_weight": "weights/best.pt",
            "best_weight_sha256": sha256_file(best_weight),
            "last_weight_sha256": sha256_file(last_weight),
            "completed_at": utc_now_iso(),
        },
    )
    return receipt_path


def _training_runtime_metadata() -> dict[str, object]:
    packages: dict[str, str | None] = {}
    for name in ("torch", "ultralytics", "opencv-python", "numpy", "PyYAML"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    torch_report: dict[str, object] = {
        "cuda_available": False,
        "cuda_version": None,
        "cudnn_version": None,
        "devices": [],
    }
    try:
        import torch

        cuda_available = bool(torch.cuda.is_available())
        torch_report = {
            "cuda_available": cuda_available,
            "cuda_version": torch.version.cuda,
            "cudnn_version": torch.backends.cudnn.version(),
            "devices": (
                [
                    {
                        "index": index,
                        "name": torch.cuda.get_device_name(index),
                        "capability": list(torch.cuda.get_device_capability(index)),
                    }
                    for index in range(torch.cuda.device_count())
                ]
                if cuda_available
                else []
            ),
        }
    except (ImportError, RuntimeError):
        pass
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": packages,
        "torch": torch_report,
    }


def evaluate_model_on_frozen_set(
    model_path: Path,
    evaluation_dir: Path,
    output_dir: Path,
    *,
    model_label: str,
    confidence: float = 0.25,
    match_iou: float = 0.5,
    device: str = "cpu",
    image_size: int = 640,
) -> Path:
    _validate_probability(confidence, "confidence")
    _validate_probability(match_iou, "match_iou")
    evaluation_root = evaluation_dir.resolve(strict=True)
    evaluation_verification = verify_frozen_evaluation_set(evaluation_root)
    weight = model_path.resolve(strict=True)
    target = output_dir.resolve()
    if target.exists():
        raise AutoLabelingError("평가 출력 디렉터리가 이미 있습니다.")
    target.mkdir(parents=True)

    data_yaml = evaluation_root / "data.yaml"
    data_yaml.write_text(
        yaml.safe_dump(
            {
                "path": str(evaluation_root),
                # 최신 Ultralytics는 val만 실행해도 두 키를 모두 요구한다.
                "train": "images",
                "val": "images",
                "nc": 1,
                "names": {0: "person"},
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    YOLO = _load_yolo()
    model = YOLO(str(weight))
    validation = model.val(
        data=str(data_yaml),
        split="val",
        conf=confidence,
        iou=match_iou,
        imgsz=image_size,
        device=device,
        classes=[0],
        # Ultralytics plotting can load a second Intel OpenMP runtime on Windows.
        # Metrics are returned independently, so keep baseline evaluation headless.
        plots=False,
        project=str(target),
        name="ultralytics-val",
        exist_ok=False,
    )
    image_paths = sorted((evaluation_root / "images").glob("*.jpg"))
    tp = fp = fn = 0
    absolute_count_errors: list[int] = []
    latencies_ms: list[float] = []
    prediction_rows: list[dict[str, Any]] = []
    for image_path in image_paths:
        started = time.perf_counter()
        results = model.predict(
            source=str(image_path),
            conf=confidence,
            imgsz=image_size,
            device=device,
            classes=[0],
            verbose=False,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        latencies_ms.append(latency_ms)
        result = results[0]
        height, width = result.orig_shape
        predictions = _prediction_boxes(result, width=width, height=height)
        ground_truth = parse_yolo_file(
            evaluation_root / "labels" / f"{image_path.stem}.txt"
        )
        frame_tp, frame_fp, frame_fn = _match_boxes(
            ground_truth, predictions, match_iou
        )
        tp += frame_tp
        fp += frame_fp
        fn += frame_fn
        absolute_count_errors.append(abs(len(predictions) - len(ground_truth)))
        prediction_rows.append(
            {
                "frame_id": image_path.stem,
                "ground_truth_count": len(ground_truth),
                "prediction_count": len(predictions),
                "true_positive": frame_tp,
                "false_positive": frame_fp,
                "false_negative": frame_fn,
                "latency_ms": round(latency_ms, 3),
            }
        )
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    evaluation_set = read_json_dict(evaluation_root / "evaluation_set.json")
    observed_hours = max(
        len(image_paths) * float(evaluation_set.get("interval_seconds", 5.0)) / 3600,
        1 / 3600,
    )
    metrics = {
        "schema_version": 2,
        "model_label": model_label,
        "model_sha256": sha256_file(weight),
        "evaluation_frozen_sha256": evaluation_verification["evaluation_frozen_sha256"],
        "evaluation_set_sha256": evaluation_verification["evaluation_set_sha256"],
        "frames_manifest_sha256": evaluation_verification["frames_manifest_sha256"],
        "confidence": confidence,
        "match_iou": match_iou,
        "image_size": image_size,
        "device": device,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "map50": float(validation.box.map50),
        "map50_95": float(validation.box.map),
        "count_mae": float(np.mean(absolute_count_errors)),
        "false_positives_per_hour": fp / observed_hours,
        "latency_ms_mean": float(np.mean(latencies_ms)),
        "latency_ms_p95": float(np.percentile(latencies_ms, 95)),
        "fps_mean": 1000 / float(np.mean(latencies_ms)),
        "model_size_bytes": weight.stat().st_size,
        "frame_count": len(image_paths),
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "evaluated_at": utc_now_iso(),
    }
    write_json(target / "metrics.json", metrics)
    (target / "predictions.jsonl").write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in prediction_rows
        ),
        encoding="utf-8",
    )
    return target / "metrics.json"


def render_result_video(
    model_path: Path,
    input_video: Path,
    output_video: Path,
    *,
    model_label: str,
    confidence: float = 0.25,
    device: str = "cpu",
    image_size: int = 640,
) -> Path:
    _validate_probability(confidence, "confidence")
    source = input_video.resolve(strict=True)
    weight = model_path.resolve(strict=True)
    target = output_video.resolve()
    if target.exists():
        raise AutoLabelingError("결과 영상이 이미 있습니다.")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise AutoLabelingError("H.264 결과 생성을 위해 ffmpeg가 필요합니다.")
    YOLO = _load_yolo()
    model = YOLO(str(weight))
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise AutoLabelingError("테스트 영상을 열 수 없습니다.")
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if fps <= 0 or width <= 0 or height <= 0:
        capture.release()
        raise AutoLabelingError("테스트 영상 메타데이터를 확인할 수 없습니다.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".render-", dir=target.parent) as temp:
        intermediate = Path(temp) / "annotated.mp4"
        writer = cv2.VideoWriter(
            str(intermediate),
            cv2.VideoWriter_fourcc(*"mp4v"),  # type: ignore[attr-defined]
            fps,
            (width, height),
        )
        if not writer.isOpened():
            capture.release()
            raise AutoLabelingError("중간 결과 영상 writer를 열 수 없습니다.")
        smoothed_fps = 0.0
        try:
            while True:
                read_ok, frame = capture.read()
                if not read_ok:
                    break
                started = time.perf_counter()
                result = model.predict(
                    source=frame,
                    conf=confidence,
                    imgsz=image_size,
                    device=device,
                    classes=[0],
                    verbose=False,
                )[0]
                elapsed = max(time.perf_counter() - started, 1e-9)
                current_fps = 1 / elapsed
                smoothed_fps = (
                    current_fps
                    if smoothed_fps == 0
                    else smoothed_fps * 0.9 + current_fps * 0.1
                )
                annotated = result.plot(labels=False, conf=True)
                count = len(result.boxes) if result.boxes is not None else 0
                cv2.rectangle(annotated, (0, 0), (width, 54), (18, 35, 63), -1)
                text = f"{model_label} | conf {confidence:.2f} | people {count} | {smoothed_fps:.1f} FPS"
                cv2.putText(
                    annotated,
                    text,
                    (16, 36),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    (255, 255, 255),
                    2,
                    cv2.LINE_AA,
                )
                writer.write(annotated)
        finally:
            writer.release()
            capture.release()
        command = [
            ffmpeg,
            "-y",
            "-i",
            str(intermediate),
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(target),
        ]
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
        if completed.returncode != 0 or not target.is_file():
            raise AutoLabelingError("ffmpeg H.264 결과 영상 생성에 실패했습니다.")
    return target


def render_comparison_video(
    left_model_path: Path,
    right_model_path: Path,
    input_video: Path,
    output_video: Path,
    *,
    left_label: str = "YOLOv8n",
    right_label: str = "YOLO11n",
    confidence: float = 0.25,
    device: str = "cpu",
    image_size: int = 640,
) -> Path:
    """같은 원본 프레임에 두 모델을 실행해 좌우 비교 H.264 영상을 만든다."""

    _validate_probability(confidence, "confidence")
    source = input_video.resolve(strict=True)
    left_weight = left_model_path.resolve(strict=True)
    right_weight = right_model_path.resolve(strict=True)
    target = output_video.resolve()
    if target.exists():
        raise AutoLabelingError("비교 결과 영상이 이미 있습니다.")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise AutoLabelingError("H.264 비교 영상 생성을 위해 ffmpeg가 필요합니다.")
    YOLO = _load_yolo()
    left_model = YOLO(str(left_weight))
    right_model = YOLO(str(right_weight))
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise AutoLabelingError("비교 테스트 영상을 열 수 없습니다.")
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if fps <= 0 or width <= 0 or height <= 0:
        capture.release()
        raise AutoLabelingError("비교 영상 메타데이터를 확인할 수 없습니다.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".compare-", dir=target.parent) as temp:
        intermediate = Path(temp) / "comparison.mp4"
        writer = cv2.VideoWriter(
            str(intermediate),
            cv2.VideoWriter_fourcc(*"mp4v"),  # type: ignore[attr-defined]
            fps,
            (width * 2, height),
        )
        if not writer.isOpened():
            capture.release()
            raise AutoLabelingError("비교 중간 영상 writer를 열 수 없습니다.")
        try:
            while True:
                read_ok, frame = capture.read()
                if not read_ok:
                    break
                left = _annotate_comparison_frame(
                    left_model,
                    frame,
                    label=left_label,
                    confidence=confidence,
                    device=device,
                    image_size=image_size,
                )
                right = _annotate_comparison_frame(
                    right_model,
                    frame,
                    label=right_label,
                    confidence=confidence,
                    device=device,
                    image_size=image_size,
                )
                writer.write(np.hstack((left, right)))
        finally:
            writer.release()
            capture.release()
        completed = subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(intermediate),
                "-an",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(target),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0 or not target.is_file():
            raise AutoLabelingError("ffmpeg 좌우 비교 영상 생성에 실패했습니다.")
    return target


def select_validation_f1_threshold(
    model_path: Path,
    deidentified_dataset_dir: Path,
    output_path: Path,
    *,
    device: str = "cpu",
    image_size: int = 640,
    match_iou: float = 0.5,
    thresholds: tuple[float, ...] = DEFAULT_F1_THRESHOLDS,
) -> Path:
    """validation 이미지에서 F1이 가장 높은 confidence를 별도 기록한다."""

    if not thresholds:
        raise AutoLabelingError("threshold 후보가 필요합니다.")
    for threshold in thresholds:
        _validate_probability(threshold, "threshold")
    _validate_probability(match_iou, "match_iou")
    dataset_root = deidentified_dataset_dir.resolve(strict=True)
    validate_privacy_export(dataset_root)
    weight = model_path.resolve(strict=True)
    image_paths = sorted((dataset_root / "images" / "val").glob("*.jpg"))
    if not image_paths:
        raise AutoLabelingError("validation 이미지가 없습니다.")
    YOLO = _load_yolo()
    model = YOLO(str(weight))
    minimum_threshold = min(thresholds)
    frames: list[tuple[list[YoloBox], list[tuple[YoloBox, float]]]] = []
    for image_path in image_paths:
        result = model.predict(
            source=str(image_path),
            conf=minimum_threshold,
            imgsz=image_size,
            device=device,
            classes=[0],
            verbose=False,
        )[0]
        height, width = result.orig_shape
        predictions: list[tuple[YoloBox, float]] = []
        if result.boxes is not None:
            coordinates = result.boxes.xyxy.cpu().numpy()
            confidences = result.boxes.conf.cpu().numpy()
            for xyxy, confidence_value in zip(coordinates, confidences, strict=True):
                left, top, right, bottom = map(float, xyxy)
                predictions.append(
                    (
                        YoloBox(
                            0,
                            ((left + right) / 2) / width,
                            ((top + bottom) / 2) / height,
                            (right - left) / width,
                            (bottom - top) / height,
                        ),
                        float(confidence_value),
                    )
                )
        ground_truth = parse_yolo_file(
            dataset_root / "labels" / "val" / f"{image_path.stem}.txt"
        )
        frames.append((ground_truth, predictions))

    sweep: list[dict[str, float | int]] = []
    for threshold in sorted(set(thresholds)):
        tp = fp = fn = 0
        for ground_truth, predictions in frames:
            selected = [box for box, score in predictions if score >= threshold]
            frame_tp, frame_fp, frame_fn = _match_boxes(
                ground_truth, selected, match_iou
            )
            tp += frame_tp
            fp += frame_fp
            fn += frame_fn
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = (
            2 * precision * recall / (precision + recall) if precision + recall else 0.0
        )
        sweep.append(
            {
                "confidence": threshold,
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "true_positive": tp,
                "false_positive": fp,
                "false_negative": fn,
            }
        )
    best = max(sweep, key=lambda item: (float(item["f1"]), -float(item["confidence"])))
    write_json(
        output_path,
        {
            "schema_version": 1,
            "model_sha256": sha256_file(weight),
            "dataset_manifest_sha256": sha256_file(dataset_root / "manifest.json"),
            "validation_frame_count": len(frames),
            "match_iou": match_iou,
            "best": best,
            "sweep": sweep,
            "selected_at": utc_now_iso(),
        },
    )
    return output_path


def compare_metric_files(metric_paths: list[Path], output_path: Path) -> Path:
    if len(metric_paths) < 2:
        raise AutoLabelingError("비교할 metric 파일은 두 개 이상이어야 합니다.")
    records = [read_json_dict(path.resolve(strict=True)) for path in metric_paths]
    keys = [
        "precision",
        "recall",
        "f1",
        "map50",
        "map50_95",
        "count_mae",
        "false_positives_per_hour",
        "latency_ms_mean",
        "fps_mean",
        "model_size_bytes",
    ]
    comparison_fields = {
        "evaluation_frozen_sha256": "동결 평가 세트",
        "confidence": "confidence",
        "match_iou": "match_iou",
        "image_size": "image_size",
        "device": "device",
        "frame_count": "평가 프레임 수",
    }
    fixed_values: dict[str, Any] = {}
    for field, label in comparison_fields.items():
        values = {record.get(field) for record in records}
        if None in values or len(values) != 1:
            raise AutoLabelingError(f"공정 비교를 위해 {label}이 같아야 합니다.")
        fixed_values[field] = next(iter(values))
    comparison = {
        "schema_version": 2,
        "generated_at": utc_now_iso(),
        "evaluation_frozen_sha256": fixed_values["evaluation_frozen_sha256"],
        "fixed_conditions": {
            field: fixed_values[field]
            for field in (
                "confidence",
                "match_iou",
                "image_size",
                "device",
                "frame_count",
            )
        },
        "models": [
            {
                "model_label": record.get("model_label"),
                **{key: record.get(key) for key in keys},
            }
            for record in records
        ],
    }
    write_json(output_path, comparison)
    return output_path


def _prediction_boxes(result: Any, *, width: int, height: int) -> list[YoloBox]:
    if result.boxes is None:
        return []
    values: list[YoloBox] = []
    for coordinates in result.boxes.xyxy.cpu().numpy():
        left, top, right, bottom = map(float, coordinates)
        values.append(
            YoloBox(
                class_id=0,
                center_x=((left + right) / 2) / width,
                center_y=((top + bottom) / 2) / height,
                width=(right - left) / width,
                height=(bottom - top) / height,
            )
        )
    return values


def _annotate_comparison_frame(
    model: Any,
    frame: Any,
    *,
    label: str,
    confidence: float,
    device: str,
    image_size: int,
) -> Any:
    started = time.perf_counter()
    result = model.predict(
        source=frame,
        conf=confidence,
        imgsz=image_size,
        device=device,
        classes=[0],
        verbose=False,
    )[0]
    inference_fps = 1 / max(time.perf_counter() - started, 1e-9)
    annotated = result.plot(labels=False, conf=True)
    count = len(result.boxes) if result.boxes is not None else 0
    width = annotated.shape[1]
    cv2.rectangle(annotated, (0, 0), (width, 54), (18, 35, 63), -1)
    cv2.putText(
        annotated,
        f"{label} | conf {confidence:.2f} | people {count} | {inference_fps:.1f} FPS",
        (16, 36),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return annotated


def _match_boxes(
    ground_truth: list[YoloBox], predictions: list[YoloBox], threshold: float
) -> tuple[int, int, int]:
    candidates = sorted(
        (
            (iou(truth, prediction), truth_index, prediction_index)
            for truth_index, truth in enumerate(ground_truth)
            for prediction_index, prediction in enumerate(predictions)
        ),
        reverse=True,
    )
    matched_truth: set[int] = set()
    matched_predictions: set[int] = set()
    for score, truth_index, prediction_index in candidates:
        if score < threshold:
            break
        if truth_index in matched_truth or prediction_index in matched_predictions:
            continue
        matched_truth.add(truth_index)
        matched_predictions.add(prediction_index)
    tp = len(matched_truth)
    return tp, len(predictions) - tp, len(ground_truth) - tp


def _load_yolo() -> Any:
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise AutoLabelingError("ultralytics가 설치되지 않았습니다.") from exc
    return YOLO


def _load_yolo_with_isolated_config(config_dir: Path) -> Any:
    """Ultralytics 최초 실행 파일을 학습 출력 root 안에 격리한다."""

    target = config_dir.resolve()
    target.mkdir(parents=True, exist_ok=True)
    previous = os.environ.get("YOLO_CONFIG_DIR")
    os.environ["YOLO_CONFIG_DIR"] = str(target)
    try:
        return _load_yolo()
    finally:
        if previous is None:
            os.environ.pop("YOLO_CONFIG_DIR", None)
        else:
            os.environ["YOLO_CONFIG_DIR"] = previous


def _validate_training_config(config: TrainingConfig) -> None:
    if (
        min(
            config.epochs,
            config.image_size,
            config.batch,
            config.workers + 1,
            config.patience,
        )
        < 1
    ):
        raise AutoLabelingError("학습 정수 설정은 허용 범위보다 커야 합니다.")
    _validate_probability(config.confidence, "confidence")


def _validate_probability(value: float, name: str) -> None:
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise AutoLabelingError(f"{name}은 0~1 유한수여야 합니다.")


def read_json_dict(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AutoLabelingError(f"{path.name}을 읽을 수 없습니다.") from exc
    if not isinstance(value, dict):
        raise AutoLabelingError(f"{path.name}은 JSON 객체여야 합니다.")
    return value
