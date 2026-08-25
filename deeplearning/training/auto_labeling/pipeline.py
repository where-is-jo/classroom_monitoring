from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import os
import platform
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any

import cv2

from .core import (
    SAFE_ID_PATTERN,
    frame_id_from_record,
    load_settings,
    read_json,
    read_jsonl,
    sha256_file,
    utc_now_iso,
    verified_frame_image_path,
    write_json,
)
from .errors import AutoLabelingError
from .evaluation import (
    freeze_evaluation_set,
    prelabel_evaluation_set,
    sample_evaluation_frames,
    verify_frozen_evaluation_set,
)
from .experiments import (
    TrainingConfig,
    select_validation_f1_threshold,
    train_person_detector,
)
from .partition import partition_sessions
from .pilot import PilotSessionPlan, prepare_clean_pilot_run
from .prelabel import run_prelabel
from .preprocessing import (
    DEFAULT_PIXELATION_BLOCK_SIZE,
    ORIGINAL_FRAME,
    UNIFORM_FULL_FRAME_PIXELATION,
    original_frame_contract,
    uniform_pixelation_contract,
)
from .privacy import export_deidentified_dataset, validate_privacy_export
from .publish import publish_dataset, validate_dataset
from .quality import FrameQualityThresholds, inspect_frame_quality
from .review import complete_review, prepare_review, verify_review_receipt
from .sessionization import scan_video_folder

yaml: Any = importlib.import_module("yaml")

TRAINING_ROOT = Path(__file__).resolve().parent.parent
N1_MODEL_RELATIVE_PATH = Path(
    "runs/person-detection/n1-yolo11n-v008-true-empty-negative17-seed42/weights/best.pt"
)
N1_MODEL_SHA256 = "7dc378a9923562e257da9bc1f649c0d39061829c47bdc121c031da4642d535e0"
PIPELINE_STATE_FILE_NAME = "pipeline-state.json"
LOCAL_CONFIG_KEYS = {
    "schema_version",
    "pipeline_id",
    "video_dir",
    "workspace_dir",
    "camera_id",
    "timezone_name",
    "expected_clip_seconds",
    "session_gap_seconds",
    "overlap_tolerance_seconds",
    "allow_approved_student_data",
    "require_session_approval_metadata",
    "n1_model_path",
    "n1_model_sha256",
    "prelabel_model_path",
    "prelabel_model_sha256",
    "prelabel_device",
    "prelabel_image_size",
    "prelabel_preprocessing_method",
    "training_export_preprocessing_method",
    "target_train_frames",
    "target_val_frames",
    "target_test_frames",
    "evaluation_interval_seconds",
    "expected_frame_width",
    "expected_frame_height",
    "force_full_review",
    "manual_excluded_frame_ids",
    "reviewer_id",
    "labelimg_executable",
    "labelimg_smoke_confirmed",
    "operator_id",
    "approved_cohort_policy",
    "pixelation_block_size",
}
TRAINING_CONFIG_KEYS = {
    "schema_version",
    "server_root",
    "dataset_dir",
    "dataset_archive",
    "archive_sha256",
    "extract_root",
    "output_root",
    "experiment_name",
    "base_model",
    "base_model_sha256",
    "mode",
    "epochs",
    "patience",
    "image_size",
    "batch",
    "device",
    "seed",
    "workers",
    "confidence",
    "require_cuda",
    "minimum_cuda_free_gib",
    "allowed_cuda_devices",
}


@dataclass(frozen=True)
class LocalPipelineConfig:
    pipeline_id: str
    video_dir: Path
    workspace_dir: Path
    camera_id: str | None
    timezone_name: str = "Asia/Seoul"
    expected_clip_seconds: float = 300.0
    session_gap_seconds: float = 60.0
    overlap_tolerance_seconds: float = 2.0
    allow_approved_student_data: bool = True
    require_session_approval_metadata: bool = True
    n1_model_path: Path = TRAINING_ROOT / N1_MODEL_RELATIVE_PATH
    n1_model_sha256: str = N1_MODEL_SHA256
    prelabel_model_path: Path | None = None
    prelabel_model_sha256: str | None = None
    prelabel_device: str = "cpu"
    prelabel_image_size: int | None = None
    prelabel_preprocessing_method: str = UNIFORM_FULL_FRAME_PIXELATION
    training_export_preprocessing_method: str = UNIFORM_FULL_FRAME_PIXELATION
    target_train_frames: int | None = None
    target_val_frames: int | None = None
    target_test_frames: int | None = None
    evaluation_interval_seconds: float = 5.0
    expected_frame_width: int | None = None
    expected_frame_height: int | None = None
    force_full_review: bool = True
    manual_excluded_frame_ids: tuple[str, ...] = ()
    reviewer_id: str | None = None
    labelimg_executable: Path | None = None
    labelimg_smoke_confirmed: bool = False
    operator_id: str = "person-detection-pipeline-auto"
    approved_cohort_policy: str = "ai-student-cohort-person-detection-v1"
    pixelation_block_size: int = DEFAULT_PIXELATION_BLOCK_SIZE


@dataclass(frozen=True)
class TrainingPipelineConfig:
    output_root: Path
    experiment_name: str
    dataset_dir: Path | None = None
    dataset_archive: Path | None = None
    archive_sha256: str | None = None
    extract_root: Path = Path("/content/datasets")
    base_model: str = "yolo11n.pt"
    base_model_sha256: str | None = None
    mode: str = "smoke-full"
    epochs: int = 50
    patience: int = 10
    image_size: int = 640
    batch: int = 16
    device: str = "auto"
    seed: int = 42
    workers: int = 2
    confidence: float = 0.25
    require_cuda: bool = True
    minimum_cuda_free_gib: float = 8.0
    allowed_cuda_devices: tuple[int, ...] | None = None


def load_local_pipeline_config(path: Path) -> LocalPipelineConfig:
    raw = _load_config(path, LOCAL_CONFIG_KEYS)
    pipeline_id = _required_text(raw, "pipeline_id")
    if SAFE_ID_PATTERN.fullmatch(pipeline_id) is None or len(pipeline_id) > 100:
        raise AutoLabelingError("pipeline_id 형식이 올바르지 않습니다.")
    exclusions = raw.get("manual_excluded_frame_ids", [])
    if not isinstance(exclusions, list) or any(
        not isinstance(value, str) or not value.strip() for value in exclusions
    ):
        raise AutoLabelingError("manual_excluded_frame_ids는 문자열 배열이어야 합니다.")
    normalized_exclusions = tuple(value.strip() for value in exclusions)
    if len(normalized_exclusions) != len(set(normalized_exclusions)):
        raise AutoLabelingError("manual_excluded_frame_ids가 중복됐습니다.")
    reviewer_id = _optional_text(raw.get("reviewer_id"))
    labelimg = _optional_path(raw.get("labelimg_executable"))
    config = LocalPipelineConfig(
        pipeline_id=pipeline_id,
        video_dir=_required_path(raw, "video_dir"),
        workspace_dir=_required_path(raw, "workspace_dir"),
        camera_id=_optional_text(raw.get("camera_id")),
        timezone_name=str(raw.get("timezone_name", "Asia/Seoul")),
        expected_clip_seconds=_positive_float(
            raw.get("expected_clip_seconds", 300.0), "expected_clip_seconds"
        ),
        session_gap_seconds=_nonnegative_float(
            raw.get("session_gap_seconds", 60.0), "session_gap_seconds"
        ),
        overlap_tolerance_seconds=_nonnegative_float(
            raw.get("overlap_tolerance_seconds", 2.0),
            "overlap_tolerance_seconds",
        ),
        allow_approved_student_data=_bool_value(
            raw.get("allow_approved_student_data", True),
            "allow_approved_student_data",
        ),
        require_session_approval_metadata=_bool_value(
            raw.get("require_session_approval_metadata", True),
            "require_session_approval_metadata",
        ),
        n1_model_path=_path_value(
            raw.get("n1_model_path", str(N1_MODEL_RELATIVE_PATH))
        ),
        n1_model_sha256=str(raw.get("n1_model_sha256", N1_MODEL_SHA256)),
        prelabel_model_path=_optional_path(raw.get("prelabel_model_path")),
        prelabel_model_sha256=_optional_text(raw.get("prelabel_model_sha256")),
        prelabel_device=str(raw.get("prelabel_device", "cpu")),
        prelabel_image_size=_optional_positive_int(
            raw.get("prelabel_image_size"), "prelabel_image_size"
        ),
        prelabel_preprocessing_method=str(
            raw.get("prelabel_preprocessing_method", UNIFORM_FULL_FRAME_PIXELATION)
        ),
        training_export_preprocessing_method=str(
            raw.get(
                "training_export_preprocessing_method",
                UNIFORM_FULL_FRAME_PIXELATION,
            )
        ),
        target_train_frames=_optional_positive_int(
            raw.get("target_train_frames"), "target_train_frames"
        ),
        target_val_frames=_optional_positive_int(
            raw.get("target_val_frames"), "target_val_frames"
        ),
        target_test_frames=_optional_positive_int(
            raw.get("target_test_frames"), "target_test_frames"
        ),
        evaluation_interval_seconds=_positive_float(
            raw.get("evaluation_interval_seconds", 5.0),
            "evaluation_interval_seconds",
        ),
        expected_frame_width=_optional_positive_int(
            raw.get("expected_frame_width"), "expected_frame_width"
        ),
        expected_frame_height=_optional_positive_int(
            raw.get("expected_frame_height"), "expected_frame_height"
        ),
        force_full_review=_bool_value(
            raw.get("force_full_review", True), "force_full_review"
        ),
        manual_excluded_frame_ids=normalized_exclusions,
        reviewer_id=reviewer_id,
        labelimg_executable=labelimg,
        labelimg_smoke_confirmed=_bool_value(
            raw.get("labelimg_smoke_confirmed", False),
            "labelimg_smoke_confirmed",
        ),
        operator_id=str(raw.get("operator_id", "person-detection-pipeline-auto")),
        approved_cohort_policy=str(
            raw.get(
                "approved_cohort_policy",
                "ai-student-cohort-person-detection-v1",
            )
        ),
        pixelation_block_size=_int_value(
            raw.get("pixelation_block_size", DEFAULT_PIXELATION_BLOCK_SIZE),
            "pixelation_block_size",
            minimum=2,
            maximum=32,
        ),
    )
    _validate_local_config(config)
    return config


def load_training_pipeline_config(path: Path) -> TrainingPipelineConfig:
    raw = _load_config(path, TRAINING_CONFIG_KEYS)
    dataset_dir = _optional_path(raw.get("dataset_dir"))
    dataset_archive = _optional_path(raw.get("dataset_archive"))
    config = TrainingPipelineConfig(
        dataset_dir=dataset_dir,
        dataset_archive=dataset_archive,
        archive_sha256=_optional_text(raw.get("archive_sha256")),
        extract_root=_path_value(raw.get("extract_root", "/content/datasets")),
        output_root=_required_path(raw, "output_root"),
        experiment_name=_required_text(raw, "experiment_name"),
        base_model=_model_reference_value(raw.get("base_model", "yolo11n.pt")),
        base_model_sha256=_optional_text(raw.get("base_model_sha256")),
        mode=str(raw.get("mode", "smoke-full")),
        epochs=_int_value(raw.get("epochs", 50), "epochs", minimum=1),
        patience=_int_value(raw.get("patience", 10), "patience", minimum=1),
        image_size=_int_value(raw.get("image_size", 640), "image_size", minimum=32),
        batch=_int_value(raw.get("batch", 16), "batch", minimum=1),
        device=str(raw.get("device", "auto")),
        seed=_int_value(raw.get("seed", 42), "seed", minimum=0),
        workers=_int_value(raw.get("workers", 2), "workers", minimum=0),
        confidence=_probability(raw.get("confidence", 0.25), "confidence"),
        require_cuda=_bool_value(raw.get("require_cuda", True), "require_cuda"),
        minimum_cuda_free_gib=_nonnegative_float(
            raw.get("minimum_cuda_free_gib", 8.0),
            "minimum_cuda_free_gib",
        ),
        allowed_cuda_devices=_optional_cuda_devices(raw.get("allowed_cuda_devices")),
    )
    _validate_training_pipeline_config(config)
    return config


def advance_local_pipeline(
    config: LocalPipelineConfig,
    *,
    complete_review_now: bool = False,
) -> dict[str, object]:
    """로컬 개인정보 경계를 지키며 가능한 단계까지 결정적으로 진행한다."""

    _reject_colab_private_processing()
    _validate_local_config(config)
    settings = load_settings()
    workspace = config.workspace_dir.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    scan_dir = workspace / "01-scan"
    partition_dir = workspace / "02-partition"
    source_runs_root = workspace / "03-source-runs"
    quality_runs_root = workspace / "04-quality-runs"
    datasets_root = workspace / "05-datasets"
    evaluation_dir = workspace / "05-fixed-test"
    export_dir = workspace / "06-colab-export"
    archive_path = workspace / "06-colab-export.zip"

    if not scan_dir.exists():
        scan_video_folder(
            config.video_dir,
            scan_dir,
            timezone_name=config.timezone_name,
            camera_id=config.camera_id,
            expected_clip_seconds=config.expected_clip_seconds,
            session_gap_seconds=config.session_gap_seconds,
            overlap_tolerance_seconds=config.overlap_tolerance_seconds,
        )
    _verify_scan_contract(scan_dir, config)
    if config.expected_frame_width is not None:
        _verify_scan_video_resolution(
            scan_dir,
            workspace / "source-resolution-report.json",
            expected_width=config.expected_frame_width,
            expected_height=config.expected_frame_height,
        )
    assignments = scan_dir / "session_assignments.csv"
    incomplete_sessions = (
        _incomplete_assignment_sessions(assignments)
        if config.require_session_approval_metadata
        else []
    )
    if incomplete_sessions:
        return _write_local_state(
            config,
            status="waiting-for-session-assignments",
            artifacts={
                "assignments": str(assignments),
                "session_timeline": str(scan_dir / "session_timeline.html"),
                "source_resolution_report": str(
                    workspace / "source-resolution-report.json"
                ),
                "prelabel_model": str(_prelabel_model_path(config)),
            },
            detail={"incomplete_session_ids": incomplete_sessions},
        )

    if not partition_dir.exists():
        partition_sessions(
            scan_dir,
            assignments,
            partition_dir,
            allow_approved_student_data=config.allow_approved_student_data,
            require_approval_metadata=config.require_session_approval_metadata,
        )
    _verify_partition_contract(partition_dir, scan_dir, assignments)

    prelabel_model_path = _prelabel_model_path(config)
    prelabel_model_sha256 = _prelabel_model_sha256(config)
    prelabel_contract = _local_preprocessing_contract(
        config.prelabel_preprocessing_method,
        config.pixelation_block_size,
    )
    if config.target_test_frames is not None:
        if not evaluation_dir.exists():
            sample_evaluation_frames(
                partition_dir / "evaluation_manifest.json",
                evaluation_dir,
                interval_seconds=config.evaluation_interval_seconds,
                target_frame_count=config.target_test_frames,
            )
        _verify_fixed_evaluation_set(
            evaluation_dir,
            partition_dir / "evaluation_manifest.json",
            config.target_test_frames,
            expected_width=config.expected_frame_width,
            expected_height=config.expected_frame_height,
        )
        prelabel_evaluation_set(
            evaluation_dir,
            prelabel_model_path,
            settings,
            device=config.prelabel_device,
            expected_model_sha256=prelabel_model_sha256,
            image_size=config.prelabel_image_size,
            input_preprocessing=prelabel_contract,
        )

    source_run = prepare_clean_source_run(
        partition_dir / "dataset_manifest.json",
        source_runs_root,
        quality_runs_root,
        pipeline_id=config.pipeline_id,
        allow_approved_student_data=config.allow_approved_student_data,
        require_approval_metadata=config.require_session_approval_metadata,
        manual_excluded_frame_ids=config.manual_excluded_frame_ids,
        target_train_frames=config.target_train_frames,
        target_val_frames=config.target_val_frames,
    )
    if config.target_train_frames is not None:
        _verify_selected_split_counts(
            source_run,
            target_train_frames=config.target_train_frames,
            target_val_frames=config.target_val_frames,
            expected_width=config.expected_frame_width,
            expected_height=config.expected_frame_height,
        )
    if config.prelabel_image_size is None:
        run_prelabel(
            source_run,
            prelabel_model_path,
            settings,
            device=config.prelabel_device,
            expected_model_sha256=prelabel_model_sha256,
            input_preprocessing=prelabel_contract,
        )
    else:
        run_prelabel(
            source_run,
            prelabel_model_path,
            settings,
            device=config.prelabel_device,
            expected_model_sha256=prelabel_model_sha256,
            image_size=config.prelabel_image_size,
            input_preprocessing=prelabel_contract,
        )
    review_dir = prepare_review(
        source_run,
        settings,
        batch_id="review-main",
        force_full=config.force_full_review,
    )
    review_receipt = review_dir / "review-completed.json"
    if not review_receipt.is_file():
        if not complete_review_now:
            return _write_local_state(
                config,
                status="waiting-for-human-review",
                artifacts={
                    "review_dir": str(review_dir),
                    "fixed_test_dir": (
                        str(evaluation_dir)
                        if config.target_test_frames is not None
                        else None
                    ),
                    "quality_report": str(source_run / "quality-report.json"),
                    "prelabel_receipt": str(source_run / "prelabel.json"),
                },
                detail={
                    "review_frame_count": _review_frame_count(review_dir),
                    "review_model": prelabel_model_path.name,
                    "model_sha256": prelabel_model_sha256,
                    "prelabel_image_size": config.prelabel_image_size,
                    "split_targets": {
                        "train": config.target_train_frames,
                        "val": config.target_val_frames,
                        "test": config.target_test_frames,
                    },
                },
            )
        if config.reviewer_id is None or config.labelimg_executable is None:
            raise AutoLabelingError(
                "검수 완료에는 reviewer_id와 labelimg_executable이 필요합니다."
            )
        complete_review(
            review_dir,
            config.reviewer_id,
            settings,
            labelimg_executable=config.labelimg_executable,
            labelimg_smoke_confirmed=config.labelimg_smoke_confirmed,
        )
    verify_review_receipt(review_dir)

    dataset_dir = publish_dataset(
        source_run,
        dataset_root=datasets_root,
        settings=settings,
    )
    dataset_report = validate_dataset(dataset_dir)
    if not export_dir.exists():
        export_deidentified_dataset(
            dataset_dir,
            export_dir,
            operator_id=config.operator_id,
            approved_cohort_policy=config.approved_cohort_policy,
            pixelation_block_size=config.pixelation_block_size,
            preprocessing_method=config.training_export_preprocessing_method,
        )
    privacy_report = validate_privacy_export(export_dir)
    _verify_export_source(export_dir, dataset_dir)
    split_counts = privacy_report.get("split_counts")
    if (
        not isinstance(split_counts, dict)
        or not isinstance(split_counts.get("train"), int)
        or not isinstance(split_counts.get("val"), int)
        or split_counts["train"] < 1
        or split_counts["val"] < 1
    ):
        raise AutoLabelingError(
            "자동 학습 export에는 train과 val 프레임이 각각 한 장 이상 필요합니다."
        )
    archive_receipt = create_dataset_archive(export_dir, archive_path)
    frozen_evaluation: dict[str, object] | None = None
    if config.target_test_frames is not None:
        if config.reviewer_id is None:
            raise AutoLabelingError("고정 Test 동결에는 reviewer_id가 필요합니다.")
        frozen_receipt = evaluation_dir / "evaluation_frozen.json"
        if not frozen_receipt.exists():
            freeze_evaluation_set(
                evaluation_dir,
                reviewer_id=config.reviewer_id,
                training_dataset_dir=dataset_dir,
            )
        frozen_evaluation = verify_frozen_evaluation_set(evaluation_dir)
        if frozen_evaluation.get("frame_count") != config.target_test_frames:
            raise AutoLabelingError("동결된 Test 프레임 수가 설정과 다릅니다.")
    return _write_local_state(
        config,
        status="ready-for-training",
        artifacts={
            "run_dir": str(source_run),
            "review_dir": str(review_dir),
            "dataset_dir": str(dataset_dir),
            "colab_export": str(export_dir),
            "dataset_archive": str(archive_path),
            "archive_receipt": str(archive_receipt),
            "fixed_test_dir": (
                str(evaluation_dir) if config.target_test_frames is not None else None
            ),
            "fixed_test_receipt": (
                str(evaluation_dir / "evaluation_frozen.json")
                if config.target_test_frames is not None
                else None
            ),
        },
        detail={
            "dataset": dataset_report,
            "privacy": privacy_report,
            "fixed_test": frozen_evaluation,
            "model_sha256": prelabel_model_sha256,
        },
    )


def prepare_clean_source_run(
    manifest_path: Path,
    source_runs_root: Path,
    quality_runs_root: Path,
    *,
    pipeline_id: str,
    allow_approved_student_data: bool,
    require_approval_metadata: bool = True,
    manual_excluded_frame_ids: tuple[str, ...] = (),
    target_train_frames: int | None = None,
    target_val_frames: int | None = None,
) -> Path:
    """프레임 추출 후 명백한 손상 프레임을 제외한 불변 파생 run을 만든다."""

    settings = load_settings()
    source_run = _prepare_run(
        manifest_path,
        settings,
        source_runs_root,
        allow_approved_student_data=allow_approved_student_data,
        require_approval_metadata=require_approval_metadata,
    )
    quality_run_id = f"{pipeline_id}-quality"
    quality_run = quality_runs_root.resolve() / quality_run_id
    if quality_run.exists():
        _verify_quality_run(
            quality_run,
            source_run,
            manual_excluded_frame_ids=manual_excluded_frame_ids,
        )
        return quality_run

    thresholds = FrameQualityThresholds()
    excluded = set(manual_excluded_frame_ids)
    found_exclusions: set[str] = set()
    eligible_counts: dict[tuple[str, str], int] = defaultdict(int)
    all_sessions: set[str] = set()
    for frame in read_jsonl(source_run / "frames.jsonl"):
        frame_id = frame_id_from_record(frame)
        session_id = str(frame.get("session_id", ""))
        split = str(frame.get("requested_split", ""))
        if not session_id or split not in {"train", "val"}:
            raise AutoLabelingError(
                "품질 선별 프레임의 세션·split이 올바르지 않습니다."
            )
        all_sessions.add(session_id)
        if frame_id in excluded:
            found_exclusions.add(frame_id)
            continue
        image_path = verified_frame_image_path(source_run, frame)
        if inspect_frame_quality(image_path, thresholds).get("passed") is not True:
            continue
        eligible_counts[(session_id, split)] += 1
    missing_exclusions = sorted(excluded - found_exclusions)
    if missing_exclusions:
        raise AutoLabelingError(
            f"수동 제외 frame_id를 추출 run에서 찾지 못했습니다: {missing_exclusions}"
        )
    selected_targets = _allocate_session_frame_targets(
        eligible_counts,
        target_train_frames=target_train_frames,
        target_val_frames=target_val_frames,
    )
    split_by_session: dict[str, str] = {}
    session_plan: dict[str, PilotSessionPlan] = {}
    for (session_id, split), _count in sorted(eligible_counts.items()):
        previous = split_by_session.setdefault(session_id, split)
        if previous != split:
            raise AutoLabelingError("같은 세션이 train과 val에 동시에 포함됐습니다.")
        selected_count = selected_targets.get((session_id, split), 0)
        if selected_count:
            session_plan[session_id] = PilotSessionPlan(split, selected_count)
    if not session_plan:
        raise AutoLabelingError("품질 검사를 통과한 프레임이 없습니다.")
    missing_sessions = sorted(all_sessions - set(session_plan))
    if missing_sessions:
        raise AutoLabelingError(
            f"정상 프레임이 하나도 남지 않은 세션이 있습니다: {missing_sessions}"
        )
    return prepare_clean_pilot_run(
        [source_run],
        quality_runs_root,
        run_id=quality_run_id,
        session_plan=session_plan,
        quality_thresholds=thresholds,
        excluded_frame_ids=manual_excluded_frame_ids,
    )


def create_dataset_archive(dataset_dir: Path, archive_path: Path) -> Path:
    """검증된 학습 데이터셋을 결정적인 ZIP과 해시 영수증으로 묶는다."""

    source = dataset_dir.resolve(strict=True)
    validate_privacy_export(source)
    target = archive_path.resolve()
    receipt_path = target.with_suffix(target.suffix + ".receipt.json")
    source_manifest_sha256 = sha256_file(source / "manifest.json")
    source_privacy_sha256 = sha256_file(source / "privacy_receipt.json")
    if target.exists() or receipt_path.exists():
        if not target.is_file() or not receipt_path.is_file():
            raise AutoLabelingError("데이터 압축 산출물이 불완전합니다.")
        receipt = read_json(receipt_path)
        if not isinstance(receipt, dict):
            raise AutoLabelingError("데이터 압축 영수증이 올바르지 않습니다.")
        expected = {
            "source_manifest_sha256": source_manifest_sha256,
            "source_privacy_receipt_sha256": source_privacy_sha256,
            "archive_sha256": sha256_file(target),
        }
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise AutoLabelingError("기존 데이터 압축 파일이 현재 export와 다릅니다.")
        return receipt_path

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".dataset-archive-", dir=target.parent
    ) as temp:
        temporary_archive = Path(temp) / target.name
        _write_deterministic_zip(source, temporary_archive, root_name=source.name)
        temporary_archive.replace(target)
    write_json(
        receipt_path,
        {
            "schema_version": 1,
            "artifact_type": "training-dataset-archive",
            "dataset_name": source.name,
            "source_manifest_sha256": source_manifest_sha256,
            "source_privacy_receipt_sha256": source_privacy_sha256,
            "archive_file_name": target.name,
            "archive_sha256": sha256_file(target),
            "created_at": utc_now_iso(),
        },
    )
    return receipt_path


def run_training_pipeline(config: TrainingPipelineConfig) -> dict[str, object]:
    """검증된 export를 smoke 확인 후 YOLO11n 정식 학습까지 실행한다."""

    _validate_training_pipeline_config(config)
    dataset = materialize_training_dataset(config)
    privacy_report = validate_privacy_export(dataset)
    if privacy_report.get("training_compatible") is not True:
        raise AutoLabelingError("학습 호환 비식별 전처리 계약이 아닙니다.")
    device = _resolve_training_device(
        config.device,
        require_cuda=config.require_cuda,
        minimum_free_bytes=_gib_to_bytes(config.minimum_cuda_free_gib),
        allowed_cuda_devices=config.allowed_cuda_devices,
    )
    active = TrainingConfig(
        epochs=config.epochs,
        image_size=config.image_size,
        batch=config.batch,
        device=device,
        seed=config.seed,
        workers=config.workers,
        patience=config.patience,
        confidence=config.confidence,
    )
    receipts: dict[str, str] = {}
    if config.mode in {"smoke", "smoke-full"}:
        smoke_config = replace(active, epochs=1, patience=1)
        smoke_name = f"smoke-{config.experiment_name}"
        smoke_receipt = _run_or_verify_training(
            dataset,
            config.output_root,
            config.base_model,
            smoke_name,
            smoke_config,
            resume=False,
        )
        receipts["smoke"] = str(smoke_receipt)
    full_receipt: Path | None = None
    if config.mode in {"full", "smoke-full", "resume"}:
        full_receipt = _run_or_verify_training(
            dataset,
            config.output_root,
            config.base_model,
            config.experiment_name,
            active,
            resume=config.mode == "resume",
        )
        receipts["full"] = str(full_receipt)
    if full_receipt is None:
        return {
            "status": "smoke-complete",
            "dataset_dir": str(dataset),
            "receipts": receipts,
            "privacy": privacy_report,
        }

    experiment_dir = full_receipt.parent
    best_weight = experiment_dir / "weights" / "best.pt"
    threshold_path = experiment_dir / "validation_f1_threshold.json"
    if threshold_path.exists():
        _verify_threshold_receipt(threshold_path, best_weight, dataset)
    else:
        select_validation_f1_threshold(
            best_weight,
            dataset,
            threshold_path,
            device=device,
            image_size=config.image_size,
        )
    model_contract_path = experiment_dir / "model_contract.json"
    _write_or_verify_model_contract(
        model_contract_path,
        best_weight=best_weight,
        dataset=dataset,
        training_receipt=full_receipt,
        privacy_report=privacy_report,
    )
    bundle_path = experiment_dir.parent / f"{experiment_dir.name}-result.zip"
    bundle_receipt = _create_training_result_bundle(experiment_dir, bundle_path)
    pipeline_receipt = experiment_dir / "pipeline-training-receipt.json"
    write_json(
        pipeline_receipt,
        {
            "schema_version": 1,
            "status": "training-complete",
            "dataset_dir": str(dataset),
            "dataset_manifest_sha256": sha256_file(dataset / "manifest.json"),
            "privacy": privacy_report,
            "experiment_name": config.experiment_name,
            "base_model": config.base_model,
            "base_model_sha256": _verified_base_model_sha256(config),
            "minimum_cuda_free_gib": config.minimum_cuda_free_gib,
            "allowed_cuda_devices": config.allowed_cuda_devices,
            "config": asdict(active),
            "training_receipt_sha256": sha256_file(full_receipt),
            "best_weight_sha256": sha256_file(best_weight),
            "model_contract_sha256": sha256_file(model_contract_path),
            "threshold_receipt_sha256": sha256_file(threshold_path),
            "result_bundle": str(bundle_path),
            "result_bundle_receipt": str(bundle_receipt),
            "completed_at": utc_now_iso(),
        },
    )
    return {
        "status": "training-complete",
        "dataset_dir": str(dataset),
        "experiment_dir": str(experiment_dir),
        "best_weight": str(best_weight),
        "threshold_receipt": str(threshold_path),
        "model_contract": str(model_contract_path),
        "result_bundle": str(bundle_path),
        "pipeline_receipt": str(pipeline_receipt),
        "receipts": receipts,
    }


def check_training_readiness(config: TrainingPipelineConfig) -> dict[str, object]:
    """학습 산출물을 만들지 않고 서버의 실행 준비 상태를 확인한다."""

    _validate_training_pipeline_config(config)
    issues: list[str] = []
    warnings: list[str] = []
    dataset = _inspect_training_input(config)
    runtime = _training_runtime_report()
    if config.dataset_archive is not None:
        warnings.append(
            "ZIP 내부 개인정보 계약의 전체 파일 검증은 안전한 압축 해제 후 "
            "pipeline-train에서 다시 수행합니다."
        )
    missing = [name for name, version in runtime["packages"].items() if version is None]
    if missing:
        issues.append(f"필수 Python 패키지가 없습니다: {', '.join(missing)}")
    python_version = tuple(int(part) for part in platform.python_version_tuple()[:2])
    if python_version < (3, 12):
        issues.append("Python 3.12 이상이 필요합니다.")

    try:
        resolved_device = _resolve_training_device(
            config.device,
            require_cuda=config.require_cuda,
            minimum_free_bytes=_gib_to_bytes(config.minimum_cuda_free_gib),
            allowed_cuda_devices=config.allowed_cuda_devices,
        )
    except AutoLabelingError as exc:
        resolved_device = None
        issues.append(str(exc))
    if resolved_device not in {None, "cpu"}:
        warnings.append(
            "GPU 선택은 현재 여유 메모리 검사이며 예약이 아닙니다. "
            "학습 직전에 다른 사용자와 사용 시간을 확인하세요."
        )

    paths: dict[str, dict[str, object]] = {
        "output_root": _path_readiness(config.output_root),
    }
    if config.dataset_archive is not None:
        paths["extract_root"] = _path_readiness(config.extract_root)
    for name, report in paths.items():
        if report["writable"] is not True:
            issues.append(f"{name}의 기존 상위 디렉터리에 쓰기 권한이 없습니다.")

    collisions = _training_output_collisions(config)
    issues.extend(collisions)
    model_path = _local_base_model_path(config.base_model)
    if model_path is None:
        warnings.append(
            "base_model이 Ultralytics 관리 이름입니다. 서버가 오프라인이면 "
            "절대 경로와 base_model_sha256을 지정하세요."
        )
        model_report: dict[str, object] = {
            "reference": config.base_model,
            "source": "ultralytics-managed",
            "sha256": None,
        }
    else:
        model_report = {
            "reference": str(model_path),
            "source": "local-file",
            "sha256": _verified_base_model_sha256(config),
        }

    return {
        "schema_version": 1,
        "status": "ready-for-training" if not issues else "not-ready",
        "artifact_writes_performed": False,
        "dataset": dataset,
        "base_model": model_report,
        "requested_device": config.device,
        "resolved_device": resolved_device,
        "minimum_cuda_free_gib": config.minimum_cuda_free_gib,
        "allowed_cuda_devices": config.allowed_cuda_devices,
        "runtime": runtime,
        "paths": paths,
        "issues": issues,
        "warnings": warnings,
    }


def _inspect_training_input(config: TrainingPipelineConfig) -> dict[str, object]:
    if config.dataset_dir is not None:
        dataset = config.dataset_dir.resolve(strict=True)
        privacy = validate_privacy_export(dataset)
        if privacy.get("training_compatible") is not True:
            raise AutoLabelingError("학습 호환 비식별 전처리 계약이 아닙니다.")
        return {
            "source": "directory",
            "path": str(dataset),
            "privacy": privacy,
        }

    if config.dataset_archive is None:
        raise AutoLabelingError("dataset_dir 또는 dataset_archive가 필요합니다.")
    archive = config.dataset_archive.resolve(strict=True)
    if archive.suffix.lower() != ".zip":
        raise AutoLabelingError("자동 학습 압축 입력은 ZIP만 지원합니다.")
    expected = _normalized_sha256(str(config.archive_sha256), "archive_sha256")
    actual = sha256_file(archive)
    if actual != expected:
        raise AutoLabelingError("학습 데이터 압축 파일 SHA-256이 다릅니다.")
    root_name = _zip_root_name(archive)
    required = {
        f"{root_name}/data.yaml",
        f"{root_name}/manifest.json",
        f"{root_name}/privacy_receipt.json",
    }
    with zipfile.ZipFile(archive) as source:
        infos = source.infolist()
        for info in infos:
            _validated_zip_member(info)
        missing = sorted(required - {info.filename.rstrip("/") for info in infos})
        if missing:
            raise AutoLabelingError(
                f"학습 데이터 ZIP에 필수 파일이 없습니다: {missing}"
            )
        uncompressed_bytes = sum(info.file_size for info in infos)
    return {
        "source": "zip",
        "path": str(archive),
        "sha256": actual,
        "archive_root": root_name,
        "member_count": len(infos),
        "uncompressed_bytes": uncompressed_bytes,
        "planned_dataset_dir": str(config.extract_root.resolve() / root_name),
    }


def _training_runtime_report() -> dict[str, Any]:
    packages = {
        "numpy": _distribution_version("numpy"),
        "opencv": _distribution_version("opencv-python")
        or _distribution_version("opencv-python-headless"),
        "pyyaml": _distribution_version("PyYAML"),
        "torch": _distribution_version("torch"),
        "ultralytics": _distribution_version("ultralytics"),
    }
    torch_report: dict[str, object] = {
        "importable": False,
        "cuda_available": False,
        "cuda_version": None,
        "cudnn_version": None,
        "devices": [],
    }
    try:
        import torch

        cuda_available = bool(torch.cuda.is_available())
        devices = []
        if cuda_available:
            for index in range(torch.cuda.device_count()):
                free_bytes, total_bytes = torch.cuda.mem_get_info(index)
                devices.append(
                    {
                        "index": index,
                        "name": torch.cuda.get_device_name(index),
                        "capability": list(torch.cuda.get_device_capability(index)),
                        "free_bytes": free_bytes,
                        "total_bytes": total_bytes,
                    }
                )
        torch_report = {
            "importable": True,
            "cuda_available": cuda_available,
            "cuda_version": torch.version.cuda,
            "cudnn_version": torch.backends.cudnn.version(),
            "devices": devices,
        }
    except (ImportError, RuntimeError):
        pass

    nvidia_smi: dict[str, object] = {
        "available": False,
        "exit_code": None,
        "gpus": [],
    }
    executable = shutil.which("nvidia-smi")
    if executable is not None:
        try:
            result = subprocess.run(
                [
                    executable,
                    "--query-gpu=index,name,driver_version,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            nvidia_smi = {
                "available": True,
                "exit_code": result.returncode,
                "gpus": [
                    line.strip() for line in result.stdout.splitlines() if line.strip()
                ],
            }
        except (OSError, subprocess.TimeoutExpired):
            nvidia_smi = {
                "available": True,
                "exit_code": None,
                "gpus": [],
            }
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": packages,
        "torch": torch_report,
        "nvidia_smi": nvidia_smi,
    }


def _distribution_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _path_readiness(path: Path) -> dict[str, object]:
    target = path.resolve()
    existing = target
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    is_directory = existing.is_dir()
    writable = is_directory and os.access(existing, os.W_OK)
    free_bytes: int | None = None
    if is_directory:
        try:
            free_bytes = shutil.disk_usage(existing).free
        except OSError:
            pass
    return {
        "path": str(target),
        "existing_ancestor": str(existing),
        "writable": writable,
        "free_bytes": free_bytes,
    }


def _training_output_collisions(config: TrainingPipelineConfig) -> list[str]:
    names: list[tuple[str, bool]] = []
    if config.mode in {"smoke", "smoke-full"}:
        names.append((f"smoke-{config.experiment_name}", False))
    if config.mode in {"full", "smoke-full"}:
        names.append((config.experiment_name, False))
    elif config.mode == "resume":
        names.append((config.experiment_name, True))
    issues: list[str] = []
    root = config.output_root.resolve()
    for name, resume in names:
        experiment = root / name
        if not experiment.exists():
            if resume:
                issues.append(f"재개할 실험 디렉터리가 없습니다: {experiment}")
            continue
        if (experiment / "training_receipt.json").is_file():
            continue
        if resume and (experiment / "weights" / "last.pt").is_file():
            continue
        issues.append(f"완료 영수증 없는 기존 실험 디렉터리가 있습니다: {experiment}")
    return issues


def materialize_training_dataset(config: TrainingPipelineConfig) -> Path:
    _validate_training_pipeline_config(config)
    if config.dataset_dir is not None:
        dataset = config.dataset_dir.resolve(strict=True)
        validate_privacy_export(dataset)
        return dataset
    if config.dataset_archive is None:
        raise AutoLabelingError("dataset_dir 또는 dataset_archive가 필요합니다.")
    archive = config.dataset_archive.resolve(strict=True)
    if archive.suffix.lower() != ".zip":
        raise AutoLabelingError("자동 학습 압축 입력은 ZIP만 지원합니다.")
    if config.archive_sha256 is not None:
        expected = _normalized_sha256(config.archive_sha256, "archive_sha256")
        if sha256_file(archive) != expected:
            raise AutoLabelingError("학습 데이터 압축 파일 SHA-256이 다릅니다.")
    root_name = _zip_root_name(archive)
    archive_manifest_sha256 = _zip_member_sha256(
        archive,
        f"{root_name}/manifest.json",
    )
    archive_privacy_sha256 = _zip_member_sha256(
        archive,
        f"{root_name}/privacy_receipt.json",
    )
    extract_root = config.extract_root.resolve()
    dataset = extract_root / root_name
    if dataset.exists():
        validate_privacy_export(dataset)
        if (
            sha256_file(dataset / "manifest.json") != archive_manifest_sha256
            or sha256_file(dataset / "privacy_receipt.json") != archive_privacy_sha256
        ):
            raise AutoLabelingError(
                "기존 압축 해제 데이터셋이 현재 ZIP 내용과 다릅니다."
            )
        return dataset
    extract_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".dataset-extract-", dir=extract_root
    ) as temp:
        temporary = Path(temp)
        _safe_extract_zip(archive, temporary)
        extracted = temporary / root_name
        validate_privacy_export(extracted)
        if (
            sha256_file(extracted / "manifest.json") != archive_manifest_sha256
            or sha256_file(extracted / "privacy_receipt.json") != archive_privacy_sha256
        ):
            raise AutoLabelingError("압축 해제 데이터셋 해시가 ZIP 내용과 다릅니다.")
        extracted.replace(dataset)
    return dataset


def read_pipeline_status(config: LocalPipelineConfig) -> dict[str, object]:
    state_path = config.workspace_dir.resolve() / PIPELINE_STATE_FILE_NAME
    if not state_path.is_file():
        return {
            "schema_version": 1,
            "pipeline_id": config.pipeline_id,
            "status": "not-started",
        }
    state = read_json(state_path)
    if not isinstance(state, dict) or state.get("pipeline_id") != config.pipeline_id:
        raise AutoLabelingError("파이프라인 상태 파일이 현재 설정과 다릅니다.")
    return state


def _prepare_run(
    manifest_path: Path,
    settings: Any,
    output_root: Path,
    *,
    allow_approved_student_data: bool,
    require_approval_metadata: bool = True,
) -> Path:
    # 테스트에서 고비용 프레임 추출 경계를 교체할 수 있도록 얇게 분리한다.
    from .prepare import prepare_run

    return prepare_run(
        manifest_path,
        settings,
        output_root=output_root,
        allow_approved_student_data=allow_approved_student_data,
        require_approval_metadata=require_approval_metadata,
    )


def _run_or_verify_training(
    dataset: Path,
    output_root: Path,
    base_model: str,
    experiment_name: str,
    config: TrainingConfig,
    *,
    resume: bool,
) -> Path:
    experiment_dir = output_root.resolve() / experiment_name
    receipt_path = experiment_dir / "training_receipt.json"
    if receipt_path.is_file():
        _verify_training_receipt(
            receipt_path,
            dataset,
            base_model,
            experiment_name,
            config,
        )
        return receipt_path
    return train_person_detector(
        base_model,
        dataset,
        output_root,
        experiment_name=experiment_name,
        config=config,
        resume=resume,
    )


def _verify_training_receipt(
    receipt_path: Path,
    dataset: Path,
    base_model: str,
    experiment_name: str,
    config: TrainingConfig,
) -> None:
    receipt = read_json(receipt_path)
    if not isinstance(receipt, dict):
        raise AutoLabelingError("기존 학습 영수증이 올바르지 않습니다.")
    if (
        receipt.get("model_name") != base_model
        or receipt.get("experiment_name") != experiment_name
        or receipt.get("config") != asdict(config)
        or receipt.get("data_yaml_sha256") != sha256_file(dataset / "data.yaml")
    ):
        raise AutoLabelingError("기존 완료 실험이 현재 학습 설정과 다릅니다.")
    best = receipt_path.parent / "weights" / "best.pt"
    last = receipt_path.parent / "weights" / "last.pt"
    if (
        not best.is_file()
        or not last.is_file()
        or receipt.get("best_weight_sha256") != sha256_file(best)
        or receipt.get("last_weight_sha256") != sha256_file(last)
    ):
        raise AutoLabelingError("기존 학습 가중치가 영수증과 다릅니다.")


def _verify_threshold_receipt(
    receipt_path: Path, best_weight: Path, dataset: Path
) -> None:
    receipt = read_json(receipt_path)
    if not isinstance(receipt, dict):
        raise AutoLabelingError("validation threshold 영수증이 올바르지 않습니다.")
    if receipt.get("model_sha256") != sha256_file(best_weight) or receipt.get(
        "dataset_manifest_sha256"
    ) != sha256_file(dataset / "manifest.json"):
        raise AutoLabelingError("validation threshold가 현재 모델·데이터와 다릅니다.")


def _write_or_verify_model_contract(
    path: Path,
    *,
    best_weight: Path,
    dataset: Path,
    training_receipt: Path,
    privacy_report: dict[str, Any],
) -> Path:
    preprocessing_contract = privacy_report.get("preprocessing_contract")
    if not isinstance(preprocessing_contract, dict):
        raise AutoLabelingError("학습 데이터셋의 전처리 계약이 없습니다.")
    expected = {
        "schema_version": 1,
        "model_sha256": sha256_file(best_weight),
        "preprocessing_contract": preprocessing_contract,
        "target_class_ids": {"0": "person"},
        "dataset_manifest_sha256": sha256_file(dataset / "manifest.json"),
        "training_receipt_sha256": sha256_file(training_receipt),
    }
    if path.exists():
        if read_json(path) != expected:
            raise AutoLabelingError("기존 모델 계약이 현재 가중치·데이터와 다릅니다.")
        return path
    write_json(path, expected)
    return path


def _create_training_result_bundle(experiment_dir: Path, target: Path) -> Path:
    required_relative_paths = (
        Path("weights/best.pt"),
        Path("weights/last.pt"),
        Path("training_receipt.json"),
        Path("validation_f1_threshold.json"),
        Path("model_contract.json"),
        Path("results.csv"),
        Path("results.png"),
        Path("args.yaml"),
    )
    missing = [
        str(path)
        for path in required_relative_paths
        if not (experiment_dir / path).is_file()
    ]
    if missing:
        raise AutoLabelingError(f"학습 결과 묶음 필수 파일이 없습니다: {missing}")
    receipt_path = target.with_suffix(target.suffix + ".receipt.json")
    fingerprints = {
        path.as_posix(): sha256_file(experiment_dir / path)
        for path in required_relative_paths
    }
    if target.exists() or receipt_path.exists():
        if not target.is_file() or not receipt_path.is_file():
            raise AutoLabelingError("학습 결과 묶음이 불완전합니다.")
        receipt = read_json(receipt_path)
        if (
            not isinstance(receipt, dict)
            or receipt.get("files") != fingerprints
            or receipt.get("archive_sha256") != sha256_file(target)
        ):
            raise AutoLabelingError("기존 학습 결과 묶음이 현재 실험과 다릅니다.")
        return receipt_path
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".training-bundle-", dir=target.parent
    ) as temp:
        staging = Path(temp) / experiment_dir.name
        for relative in required_relative_paths:
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((experiment_dir / relative).read_bytes())
        temporary_archive = Path(temp) / target.name
        _write_deterministic_zip(staging, temporary_archive, root_name=staging.name)
        temporary_archive.replace(target)
    write_json(
        receipt_path,
        {
            "schema_version": 1,
            "artifact_type": "person-detector-training-result",
            "experiment_name": experiment_dir.name,
            "files": fingerprints,
            "archive_sha256": sha256_file(target),
            "created_at": utc_now_iso(),
        },
    )
    return receipt_path


def _verify_scan_contract(scan_dir: Path, config: LocalPipelineConfig) -> None:
    manifest = read_json(scan_dir / "session_manifest.json")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise AutoLabelingError("기존 세션 스캔 manifest가 올바르지 않습니다.")
    expected = {
        "input_root": str(config.video_dir.resolve(strict=True)),
        "timezone": config.timezone_name,
        "camera_id": config.camera_id,
        "expected_clip_seconds": config.expected_clip_seconds,
        "session_gap_seconds": config.session_gap_seconds,
        "overlap_tolerance_seconds": config.overlap_tolerance_seconds,
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise AutoLabelingError("기존 세션 스캔이 현재 파이프라인 설정과 다릅니다.")
    inventory = read_jsonl(scan_dir / "video_inventory.jsonl")
    recorded = {str(item.get("relative_path", "")) for item in inventory}
    current = {
        path.relative_to(config.video_dir.resolve(strict=True)).as_posix()
        for path in config.video_dir.resolve(strict=True).rglob("*")
        if path.is_file() and path.suffix.lower() == ".mp4"
    }
    if recorded != current:
        raise AutoLabelingError("스캔 이후 원본 MP4 파일 집합이 변경됐습니다.")


def _incomplete_assignment_sessions(path: Path) -> list[str]:
    required_columns = {
        "session_id",
        "role",
        "approval_reference",
        "consent_scope",
        "retention_expires_at",
        "subject_category",
    }
    approval_fields = required_columns - {"session_id", "role"}
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None or not {"session_id", "role"}.issubset(
                reader.fieldnames
            ):
                raise AutoLabelingError("session_assignments.csv 필수 열이 없습니다.")
            missing_approval_columns = approval_fields - set(reader.fieldnames)
            incomplete: list[str] = []
            for row in reader:
                session_id = str(row.get("session_id", "")).strip()
                role = str(row.get("role", "")).strip()
                if not role or (
                    role != "excluded"
                    and (
                        bool(missing_approval_columns)
                        or any(
                            not str(row.get(field, "")).strip()
                            for field in approval_fields
                        )
                    )
                ):
                    incomplete.append(session_id)
            return sorted(incomplete)
    except OSError as exc:
        raise AutoLabelingError("session_assignments.csv를 읽을 수 없습니다.") from exc


def _verify_scan_video_resolution(
    scan_dir: Path,
    report_path: Path,
    *,
    expected_width: int,
    expected_height: int | None,
) -> None:
    if expected_height is None:
        raise AutoLabelingError("expected_frame_height가 필요합니다.")
    inventory_path = scan_dir / "video_inventory.jsonl"
    inventory_sha256 = sha256_file(inventory_path)
    inventory = [
        record
        for record in read_jsonl(inventory_path)
        if record.get("status") == "accepted"
    ]
    if report_path.exists():
        report = read_json(report_path)
        report_items = report.get("items") if isinstance(report, dict) else None
        item_by_source = (
            {
                str(item.get("source_id", "")): item
                for item in report_items
                if isinstance(item, dict)
            }
            if isinstance(report_items, list)
            else {}
        )
        if (
            not isinstance(report, dict)
            or report.get("status") != "passed"
            or report.get("inventory_sha256") != inventory_sha256
            or report.get("expected_width") != expected_width
            or report.get("expected_height") != expected_height
            or report.get("video_count") != len(inventory)
            or len(item_by_source) != len(inventory)
            or any(
                (
                    item_by_source.get(str(record.get("source_id", "")), {}).get(
                        "source_sha256"
                    )
                    != record.get("sha256")
                    or item_by_source.get(str(record.get("source_id", "")), {}).get(
                        "width"
                    )
                    != expected_width
                    or item_by_source.get(str(record.get("source_id", "")), {}).get(
                        "height"
                    )
                    != expected_height
                    or item_by_source.get(str(record.get("source_id", "")), {}).get(
                        "first_frame_decoded"
                    )
                    is not True
                )
                for record in inventory
            )
        ):
            raise AutoLabelingError(
                "기존 원본 해상도 보고서가 현재 스캔·설정과 다릅니다."
            )
        return

    items: list[dict[str, object]] = []
    for record in inventory:
        video_path = Path(str(record.get("absolute_path", ""))).resolve(strict=True)
        capture = cv2.VideoCapture(str(video_path))
        try:
            if not capture.isOpened():
                raise AutoLabelingError("원본 영상 스트림을 열 수 없습니다.")
            width = round(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = round(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = float(capture.get(cv2.CAP_PROP_FPS))
            decoded, first_frame = capture.read()
            if (
                width != expected_width
                or height != expected_height
                or not decoded
                or first_frame is None
                or first_frame.shape[:2] != (expected_height, expected_width)
                or not (fps > 0)
            ):
                raise AutoLabelingError(
                    f"source_id={record.get('source_id')}: "
                    "원본 영상 해상도·FPS·첫 프레임 디코딩 계약을 통과하지 못했습니다."
                )
            items.append(
                {
                    "source_id": record.get("source_id"),
                    "relative_path": record.get("relative_path"),
                    "source_sha256": record.get("sha256"),
                    "width": width,
                    "height": height,
                    "fps": round(fps, 6),
                    "first_frame_decoded": True,
                }
            )
        finally:
            capture.release()
    if not items:
        raise AutoLabelingError("해상도를 검사할 원본 영상이 없습니다.")
    write_json(
        report_path,
        {
            "schema_version": 1,
            "status": "passed",
            "inventory_sha256": inventory_sha256,
            "expected_width": expected_width,
            "expected_height": expected_height,
            "video_count": len(items),
            "items": items,
            "created_at": utc_now_iso(),
        },
    )


def _verify_partition_contract(
    partition_dir: Path,
    scan_dir: Path,
    assignments_path: Path,
) -> None:
    split_receipt = read_json(partition_dir / "split_receipt.json")
    scan_manifest = read_json(scan_dir / "session_manifest.json")
    leak_check = read_json(partition_dir / "leak_check.json")
    if (
        not isinstance(split_receipt, dict)
        or not isinstance(scan_manifest, dict)
        or split_receipt.get("assignments_sha256") != sha256_file(assignments_path)
        or split_receipt.get("scan_fingerprint")
        != scan_manifest.get("scan_fingerprint")
    ):
        raise AutoLabelingError(
            "기존 세션 분할이 현재 스캔·배정표와 다릅니다. "
            "새 pipeline_id와 workspace_dir을 사용하세요."
        )
    if not isinstance(leak_check, dict) or leak_check.get("passed") is not True:
        raise AutoLabelingError("세션 분할 누출 검사가 통과하지 않았습니다.")


def _verify_quality_run(
    quality_run: Path,
    source_run: Path,
    *,
    manual_excluded_frame_ids: tuple[str, ...],
) -> None:
    run = read_json(quality_run / "run.json")
    report = read_json(quality_run / "quality-report.json")
    if not isinstance(run, dict) or not isinstance(report, dict):
        raise AutoLabelingError("기존 품질 선별 run이 올바르지 않습니다.")
    expected_source = {
        "run_id": read_json(source_run / "run.json").get("run_id"),
        "run_sha256": sha256_file(source_run / "run.json"),
        "frames_sha256": sha256_file(source_run / "frames.jsonl"),
    }
    source_runs = run.get("source_runs")
    manual_ids = sorted(
        str(item.get("frame_id"))
        for item in report.get("manual_exclusions", [])
        if isinstance(item, dict)
    )
    if (
        source_runs != [expected_source]
        or report.get("status") != "passed"
        or manual_ids != sorted(manual_excluded_frame_ids)
    ):
        raise AutoLabelingError("기존 품질 선별 run이 현재 입력·제외 목록과 다릅니다.")
    frames = read_jsonl(quality_run / "frames.jsonl")
    if run.get("frame_count") != len(frames):
        raise AutoLabelingError("품질 선별 run의 프레임 수가 다릅니다.")
    for frame in frames:
        verified_frame_image_path(quality_run, frame)


def _allocate_session_frame_targets(
    eligible_counts: dict[tuple[str, str], int],
    *,
    target_train_frames: int | None,
    target_val_frames: int | None,
) -> dict[tuple[str, str], int]:
    targets = {"train": target_train_frames, "val": target_val_frames}
    allocations: dict[tuple[str, str], int] = {}
    for split, requested_target in targets.items():
        capacities = {
            session_id: count
            for (session_id, candidate_split), count in eligible_counts.items()
            if candidate_split == split and count > 0
        }
        if not capacities:
            raise AutoLabelingError(f"정상 {split} 프레임이 없습니다.")
        target = (
            sum(capacities.values()) if requested_target is None else requested_target
        )
        if target > sum(capacities.values()):
            raise AutoLabelingError(
                f"정상 {split} 프레임 {sum(capacities.values())}장은 "
                f"목표 {target}장보다 적습니다."
            )
        session_ids = sorted(capacities)
        if target < len(session_ids):
            raise AutoLabelingError(
                f"{split} 목표 프레임 수가 세션 수보다 적습니다. "
                "모든 세션에서 한 장 이상 선택해야 합니다."
            )
        split_allocations = {session_id: 1 for session_id in session_ids}
        remaining = target - len(session_ids)
        while remaining:
            progressed = False
            for session_id in session_ids:
                if split_allocations[session_id] >= capacities[session_id]:
                    continue
                split_allocations[session_id] += 1
                remaining -= 1
                progressed = True
                if remaining == 0:
                    break
            if not progressed:
                raise AutoLabelingError(f"{split} 프레임 목표 수를 배분할 수 없습니다.")
        allocations.update(
            {
                (session_id, split): count
                for session_id, count in split_allocations.items()
            }
        )
    return allocations


def _verify_selected_split_counts(
    run_dir: Path,
    *,
    target_train_frames: int | None,
    target_val_frames: int | None,
    expected_width: int | None,
    expected_height: int | None,
) -> None:
    frames = read_jsonl(run_dir / "frames.jsonl")
    counts = {
        split: sum(frame.get("requested_split") == split for frame in frames)
        for split in ("train", "val")
    }
    expected = {"train": target_train_frames, "val": target_val_frames}
    for split, target in expected.items():
        if counts[split] < 1:
            raise AutoLabelingError(f"선택 run에 {split} 프레임이 없습니다.")
        if target is not None and counts[split] != target:
            raise AutoLabelingError(f"선택 run의 {split} 프레임 수가 목표와 다릅니다.")
    session_splits: dict[str, str] = {}
    for frame in frames:
        session_id = str(frame.get("session_id", ""))
        split = str(frame.get("requested_split", ""))
        previous = session_splits.setdefault(session_id, split)
        if previous != split:
            raise AutoLabelingError("선택 run의 같은 세션이 여러 split에 있습니다.")
        if expected_width is not None and expected_height is not None:
            image = cv2.imread(str(verified_frame_image_path(run_dir, frame)))
            if image is None or image.shape[:2] != (expected_height, expected_width):
                raise AutoLabelingError(
                    "선택 run 이미지의 해상도가 원본 프레임 계약과 다릅니다."
                )


def _verify_fixed_evaluation_set(
    evaluation_dir: Path,
    source_manifest_path: Path,
    target_frame_count: int,
    *,
    expected_width: int | None,
    expected_height: int | None,
) -> None:
    metadata = read_json(evaluation_dir / "evaluation_set.json")
    records = read_jsonl(evaluation_dir / "evaluation_frames.jsonl")
    if (
        not isinstance(metadata, dict)
        or metadata.get("source_manifest_sha256") != sha256_file(source_manifest_path)
        or metadata.get("target_frame_count") != target_frame_count
        or metadata.get("frame_count") != target_frame_count
        or len(records) != target_frame_count
    ):
        raise AutoLabelingError(
            "기존 고정 Test가 현재 manifest 또는 목표 장수와 다릅니다."
        )
    frame_ids: set[str] = set()
    for record in records:
        frame_id = str(record.get("frame_id", ""))
        if not frame_id or frame_id in frame_ids:
            raise AutoLabelingError("고정 Test frame_id가 비었거나 중복됐습니다.")
        frame_ids.add(frame_id)
        image_path = evaluation_dir / "images" / f"{frame_id}.jpg"
        if sha256_file(image_path) != record.get("image_sha256"):
            raise AutoLabelingError("고정 Test 이미지가 선택 후 변경됐습니다.")
        if expected_width is not None and expected_height is not None:
            image = cv2.imread(str(image_path))
            if image is None or image.shape[:2] != (expected_height, expected_width):
                raise AutoLabelingError(
                    "고정 Test 이미지의 해상도가 원본 프레임 계약과 다릅니다."
                )


def _prelabel_model_path(config: LocalPipelineConfig) -> Path:
    return config.prelabel_model_path or config.n1_model_path


def _prelabel_model_sha256(config: LocalPipelineConfig) -> str:
    value = config.prelabel_model_sha256 or config.n1_model_sha256
    return _normalized_sha256(value, "prelabel_model_sha256")


def _local_preprocessing_contract(
    method: str,
    pixelation_block_size: int,
) -> dict[str, object]:
    if method == ORIGINAL_FRAME:
        return original_frame_contract()
    if method == UNIFORM_FULL_FRAME_PIXELATION:
        return uniform_pixelation_contract(pixelation_block_size)
    raise AutoLabelingError(
        "전처리 방법은 original-frame-v1 또는 "
        "uniform-full-frame-pixelation-v1이어야 합니다."
    )


def _verify_export_source(export_dir: Path, dataset_dir: Path) -> None:
    receipt = read_json(export_dir / "privacy_receipt.json")
    if not isinstance(receipt, dict) or receipt.get(
        "source_manifest_sha256"
    ) != sha256_file(dataset_dir / "manifest.json"):
        raise AutoLabelingError("기존 Colab export가 현재 발행 데이터셋과 다릅니다.")


def _review_frame_count(review_dir: Path) -> int:
    batch = read_json(review_dir / "review-batch.json")
    frames = batch.get("frame_ids") if isinstance(batch, dict) else None
    if not isinstance(frames, list):
        raise AutoLabelingError("검수 배치 프레임 목록이 올바르지 않습니다.")
    return len(frames)


def _write_local_state(
    config: LocalPipelineConfig,
    *,
    status: str,
    artifacts: dict[str, object],
    detail: dict[str, object],
) -> dict[str, object]:
    state = {
        "schema_version": 1,
        "pipeline_id": config.pipeline_id,
        "status": status,
        "prelabel_model_path": str(_prelabel_model_path(config)),
        "prelabel_model_sha256": _prelabel_model_sha256(config),
        "prelabel_image_size": config.prelabel_image_size,
        "preprocessing_method": config.prelabel_preprocessing_method,
        "pixelation_block_size": (
            config.pixelation_block_size
            if config.prelabel_preprocessing_method == UNIFORM_FULL_FRAME_PIXELATION
            else None
        ),
        "split_targets": {
            "train": config.target_train_frames,
            "val": config.target_val_frames,
            "test": config.target_test_frames,
        },
        "artifacts": artifacts,
        "detail": detail,
        "updated_at": utc_now_iso(),
    }
    write_json(config.workspace_dir.resolve() / PIPELINE_STATE_FILE_NAME, state)
    return state


def _write_deterministic_zip(source: Path, target: Path, *, root_name: str) -> None:
    with zipfile.ZipFile(
        target,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in sorted(source.rglob("*"), key=lambda item: item.as_posix()):
            if not path.is_file():
                continue
            if path.is_symlink():
                raise AutoLabelingError(
                    "압축 입력에는 심볼릭 링크를 사용할 수 없습니다."
                )
            relative = path.relative_to(source).as_posix()
            info = zipfile.ZipInfo(f"{root_name}/{relative}")
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes(), compresslevel=9)


def _zip_root_name(archive_path: Path) -> str:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            roots: set[str] = set()
            for info in archive.infolist():
                path = _validated_zip_member(info)
                if path.parts:
                    roots.add(path.parts[0])
    except zipfile.BadZipFile as exc:
        raise AutoLabelingError("학습 데이터 ZIP 형식이 올바르지 않습니다.") from exc
    if len(roots) != 1:
        raise AutoLabelingError("학습 데이터 ZIP에는 최상위 폴더가 하나여야 합니다.")
    root = next(iter(roots))
    if SAFE_ID_PATTERN.fullmatch(root) is None:
        raise AutoLabelingError("학습 데이터 ZIP 최상위 폴더 이름이 올바르지 않습니다.")
    return root


def _safe_extract_zip(archive_path: Path, output_dir: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            _validated_zip_member(info)
        archive.extractall(output_dir)


def _zip_member_sha256(archive_path: Path, member_name: str) -> str:
    digest = hashlib.sha256()
    try:
        with (
            zipfile.ZipFile(archive_path) as archive,
            archive.open(member_name) as source,
        ):
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        raise AutoLabelingError(
            f"학습 데이터 ZIP에 필수 파일이 없습니다: {member_name}"
        ) from exc
    return digest.hexdigest()


def _validated_zip_member(info: zipfile.ZipInfo) -> PurePosixPath:
    if "\\" in info.filename or "\x00" in info.filename:
        raise AutoLabelingError("학습 데이터 ZIP에 안전하지 않은 경로가 있습니다.")
    path = PurePosixPath(info.filename)
    if (
        path.is_absolute()
        or not path.parts
        or ".." in path.parts
        or any(":" in part for part in path.parts)
    ):
        raise AutoLabelingError("학습 데이터 ZIP에 안전하지 않은 경로가 있습니다.")
    file_type = (info.external_attr >> 16) & 0o170000
    if file_type == stat.S_IFLNK:
        raise AutoLabelingError("학습 데이터 ZIP에는 심볼릭 링크를 사용할 수 없습니다.")
    return path


def _resolve_training_device(
    value: str,
    *,
    require_cuda: bool,
    minimum_free_bytes: int = 0,
    allowed_cuda_devices: tuple[int, ...] | None = None,
) -> str:
    normalized = value.strip().lower()
    if not normalized:
        raise AutoLabelingError("학습 device가 비어 있습니다.")
    if normalized == "cpu":
        if require_cuda:
            raise AutoLabelingError("정식 파이프라인은 CUDA GPU가 필요합니다.")
        return "cpu"
    try:
        import torch
    except ImportError as exc:
        raise AutoLabelingError("학습 장치 확인을 위해 torch가 필요합니다.") from exc
    cuda_available = bool(torch.cuda.is_available())
    device_count = int(torch.cuda.device_count()) if cuda_available else 0
    allowed = (
        tuple(range(device_count))
        if allowed_cuda_devices is None
        else allowed_cuda_devices
    )
    if cuda_available:
        if not allowed:
            raise AutoLabelingError("사용하도록 승인된 CUDA 장치가 없습니다.")
        if any(index >= device_count for index in allowed):
            raise AutoLabelingError(
                "allowed_cuda_devices에 현재 보이지 않는 CUDA 장치가 있습니다. "
                f"사용 가능 장치 수: {device_count}"
            )
    if normalized == "auto":
        if cuda_available:
            free_by_device = _cuda_free_bytes_by_device(torch)
            selected, free_bytes = max(
                ((index, free_by_device[index]) for index in allowed),
                key=lambda item: (item[1], -item[0]),
            )
            _validate_cuda_free_memory(
                {selected: free_bytes},
                minimum_free_bytes,
            )
            return str(selected)
        if require_cuda:
            raise AutoLabelingError("CUDA GPU를 사용할 수 없습니다.")
        return "cpu"
    cuda_indices = _requested_cuda_indices(normalized)
    if cuda_indices is not None:
        if not cuda_available:
            raise AutoLabelingError("CUDA GPU를 사용할 수 없습니다.")
        if any(index >= device_count for index in cuda_indices):
            raise AutoLabelingError(
                f"요청한 CUDA 장치가 없습니다. 사용 가능 장치 수: {device_count}"
            )
        disallowed = sorted(set(cuda_indices) - set(allowed))
        if disallowed:
            raise AutoLabelingError(
                f"승인되지 않은 CUDA 장치를 요청했습니다: {disallowed}"
            )
        free_by_device = _cuda_free_bytes_by_device(torch)
        _validate_cuda_free_memory(
            {index: free_by_device[index] for index in cuda_indices},
            minimum_free_bytes,
        )
        return value.strip()
    if require_cuda:
        raise AutoLabelingError("require_cuda=true이면 CUDA 장치를 지정해야 합니다.")
    return value.strip()


def _cuda_free_bytes_by_device(torch: Any) -> dict[int, int]:
    try:
        return {
            index: int(torch.cuda.mem_get_info(index)[0])
            for index in range(torch.cuda.device_count())
        }
    except RuntimeError as exc:
        raise AutoLabelingError("CUDA GPU 여유 메모리를 확인할 수 없습니다.") from exc


def _validate_cuda_free_memory(
    free_by_device: dict[int, int],
    minimum_free_bytes: int,
) -> None:
    insufficient = {
        index: free_bytes
        for index, free_bytes in free_by_device.items()
        if free_bytes < minimum_free_bytes
    }
    if insufficient:
        details = ", ".join(
            f"GPU {index}={free_bytes / (1024**3):.1f} GiB"
            for index, free_bytes in sorted(insufficient.items())
        )
        raise AutoLabelingError(
            "CUDA GPU 여유 메모리가 최소 기준보다 작습니다: "
            f"{details} < {minimum_free_bytes / (1024**3):.1f} GiB"
        )


def _gib_to_bytes(value: float) -> int:
    return int(value * (1024**3))


def _requested_cuda_indices(value: str) -> tuple[int, ...] | None:
    if value == "cuda":
        return (0,)
    if value.startswith("cuda:"):
        value = value.removeprefix("cuda:")
    parts = value.split(",")
    if not parts or any(not part.isdigit() for part in parts):
        return None
    indices = tuple(int(part) for part in parts)
    if len(indices) != len(set(indices)):
        raise AutoLabelingError("학습 CUDA device가 중복됐습니다.")
    return indices


def _validate_local_config(config: LocalPipelineConfig) -> None:
    if SAFE_ID_PATTERN.fullmatch(config.pipeline_id) is None:
        raise AutoLabelingError("pipeline_id 형식이 올바르지 않습니다.")
    video = config.video_dir.resolve(strict=False)
    workspace = config.workspace_dir.resolve()
    if (
        video == workspace
        or _is_relative_to(video, workspace)
        or _is_relative_to(workspace, video)
    ):
        raise AutoLabelingError(
            "원본 영상 폴더와 파이프라인 출력 폴더는 분리해야 합니다."
        )
    _normalized_sha256(config.n1_model_sha256, "n1_model_sha256")
    if config.prelabel_model_path is None and config.prelabel_model_sha256 is not None:
        raise AutoLabelingError(
            "prelabel_model_sha256를 쓰려면 prelabel_model_path가 필요합니다."
        )
    if config.prelabel_model_path is not None and config.prelabel_model_sha256 is None:
        raise AutoLabelingError(
            "prelabel_model_path를 쓰려면 prelabel_model_sha256가 필요합니다."
        )
    if config.prelabel_model_sha256 is not None:
        _normalized_sha256(
            config.prelabel_model_sha256,
            "prelabel_model_sha256",
        )
    if config.prelabel_image_size is not None and (
        config.prelabel_image_size < 32 or config.prelabel_image_size % 32 != 0
    ):
        raise AutoLabelingError("prelabel_image_size는 32 이상의 32 배수여야 합니다.")
    _local_preprocessing_contract(
        config.prelabel_preprocessing_method,
        config.pixelation_block_size,
    )
    _local_preprocessing_contract(
        config.training_export_preprocessing_method,
        config.pixelation_block_size,
    )
    if (
        config.prelabel_preprocessing_method
        != config.training_export_preprocessing_method
    ):
        raise AutoLabelingError(
            "자동 라벨링과 학습 export의 전처리 방법은 같아야 합니다."
        )
    if (config.target_train_frames is None) != (config.target_val_frames is None):
        raise AutoLabelingError(
            "target_train_frames와 target_val_frames는 함께 지정해야 합니다."
        )
    if (config.expected_frame_width is None) != (config.expected_frame_height is None):
        raise AutoLabelingError(
            "expected_frame_width와 expected_frame_height는 함께 지정해야 합니다."
        )
    if not config.operator_id.strip() or not config.approved_cohort_policy.strip():
        raise AutoLabelingError("개인정보 반출 작업자와 승인 정책 참조가 필요합니다.")
    uniform_pixelation_contract(config.pixelation_block_size)


def _validate_training_pipeline_config(config: TrainingPipelineConfig) -> None:
    if (config.dataset_dir is None) == (config.dataset_archive is None):
        raise AutoLabelingError(
            "dataset_dir과 dataset_archive 중 정확히 하나를 지정하세요."
        )
    if SAFE_ID_PATTERN.fullmatch(config.experiment_name) is None:
        raise AutoLabelingError("experiment_name 형식이 올바르지 않습니다.")
    if Path(config.base_model).name not in {"yolo11n.pt", "yolo26n.pt"}:
        raise AutoLabelingError(
            "자동 학습 기준 모델은 yolo11n.pt 또는 yolo26n.pt여야 합니다."
        )
    if config.mode not in {"smoke", "full", "smoke-full", "resume"}:
        raise AutoLabelingError(
            "학습 mode는 smoke/full/smoke-full/resume이어야 합니다."
        )
    if config.minimum_cuda_free_gib < 0:
        raise AutoLabelingError("minimum_cuda_free_gib는 0 이상이어야 합니다.")
    if config.allowed_cuda_devices is not None:
        if not config.allowed_cuda_devices:
            raise AutoLabelingError("allowed_cuda_devices는 비어 있을 수 없습니다.")
        if any(index < 0 for index in config.allowed_cuda_devices):
            raise AutoLabelingError("allowed_cuda_devices는 0 이상의 정수여야 합니다.")
        if len(config.allowed_cuda_devices) != len(set(config.allowed_cuda_devices)):
            raise AutoLabelingError("allowed_cuda_devices가 중복됐습니다.")
    if config.archive_sha256 is not None:
        _normalized_sha256(config.archive_sha256, "archive_sha256")
    if config.dataset_archive is not None and config.archive_sha256 is None:
        raise AutoLabelingError(
            "dataset_archive를 사용할 때 archive_sha256이 필요합니다."
        )
    model_path = _local_base_model_path(config.base_model)
    if model_path is None:
        if config.base_model_sha256 is not None:
            raise AutoLabelingError(
                "base_model_sha256을 쓰려면 base_model에 로컬 절대 경로를 지정하세요."
            )
    else:
        if config.base_model_sha256 is None:
            raise AutoLabelingError(
                "로컬 base_model을 사용할 때 base_model_sha256이 필요합니다."
            )
        expected = _normalized_sha256(
            config.base_model_sha256,
            "base_model_sha256",
        )
        if sha256_file(model_path) != expected:
            raise AutoLabelingError("기준 모델 파일 SHA-256이 다릅니다.")


def _reject_colab_private_processing() -> None:
    if os.environ.get("COLAB_RELEASE_TAG") or os.environ.get("COLAB_BACKEND_VERSION"):
        raise AutoLabelingError(
            "원본 영상 로컬 파이프라인은 Colab에서 실행할 수 없습니다."
        )


def _load_config(path: Path, allowed_keys: set[str]) -> dict[str, object]:
    try:
        raw = yaml.safe_load(path.resolve(strict=True).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise AutoLabelingError("파이프라인 설정 YAML을 읽을 수 없습니다.") from exc
    if not isinstance(raw, dict):
        raise AutoLabelingError("파이프라인 설정은 YAML 객체여야 합니다.")
    if any(not isinstance(key, str) for key in raw):
        raise AutoLabelingError("파이프라인 설정 필드 이름은 문자열이어야 합니다.")
    unknown = sorted(set(raw) - allowed_keys)
    if unknown:
        raise AutoLabelingError(f"알 수 없는 파이프라인 설정 필드입니다: {unknown}")
    if raw.get("schema_version") != 1:
        raise AutoLabelingError("지원하지 않는 파이프라인 설정 버전입니다.")
    return raw


def _required_text(raw: dict[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AutoLabelingError(f"{key}가 필요합니다.")
    return value.strip()


def _optional_text(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise AutoLabelingError("선택 문자열 설정 형식이 올바르지 않습니다.")
    return value.strip() or None


def _optional_cuda_devices(value: object) -> tuple[int, ...] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not value:
        raise AutoLabelingError(
            "allowed_cuda_devices는 비어 있지 않은 정수 배열이어야 합니다."
        )
    if any(
        isinstance(index, bool) or not isinstance(index, int) or index < 0
        for index in value
    ):
        raise AutoLabelingError(
            "allowed_cuda_devices는 0 이상의 정수 배열이어야 합니다."
        )
    devices = tuple(value)
    if len(devices) != len(set(devices)):
        raise AutoLabelingError("allowed_cuda_devices가 중복됐습니다.")
    return devices


def _required_path(raw: dict[str, object], key: str) -> Path:
    if key not in raw:
        raise AutoLabelingError(f"{key}가 필요합니다.")
    return _path_value(raw[key])


def _optional_path(value: object) -> Path | None:
    if value is None or value == "":
        return None
    return _path_value(value)


def _optional_positive_int(value: object, key: str) -> int | None:
    if value is None:
        return None
    return _int_value(value, key, minimum=1)


def _path_value(value: object) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise AutoLabelingError("경로 설정은 비어 있지 않은 문자열이어야 합니다.")
    expanded = os.path.expandvars(os.path.expanduser(value.strip()))
    path = Path(expanded)
    return path if path.is_absolute() else TRAINING_ROOT / path


def _model_reference_value(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AutoLabelingError("base_model은 비어 있지 않은 문자열이어야 합니다.")
    text = value.strip()
    if text in {"yolo11n.pt", "yolo26n.pt"}:
        return text
    return str(_path_value(text))


def _local_base_model_path(reference: str) -> Path | None:
    if reference in {"yolo11n.pt", "yolo26n.pt"}:
        return None
    try:
        path = Path(reference).resolve(strict=True)
    except OSError as exc:
        raise AutoLabelingError("로컬 기준 모델 파일을 찾을 수 없습니다.") from exc
    if not path.is_file():
        raise AutoLabelingError("로컬 기준 모델 경로가 파일이 아닙니다.")
    return path


def _verified_base_model_sha256(config: TrainingPipelineConfig) -> str | None:
    path = _local_base_model_path(config.base_model)
    return sha256_file(path) if path is not None else None


def _bool_value(value: object, key: str) -> bool:
    if not isinstance(value, bool):
        raise AutoLabelingError(f"{key}는 true/false여야 합니다.")
    return value


def _int_value(
    value: object,
    key: str,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AutoLabelingError(f"{key}는 정수여야 합니다.")
    if value < minimum or (maximum is not None and value > maximum):
        raise AutoLabelingError(f"{key}가 허용 범위를 벗어났습니다.")
    return value


def _positive_float(value: object, key: str) -> float:
    result = _number(value, key)
    if result <= 0:
        raise AutoLabelingError(f"{key}는 0보다 커야 합니다.")
    return result


def _nonnegative_float(value: object, key: str) -> float:
    result = _number(value, key)
    if result < 0:
        raise AutoLabelingError(f"{key}는 0 이상이어야 합니다.")
    return result


def _probability(value: object, key: str) -> float:
    result = _number(value, key)
    if not 0 <= result <= 1:
        raise AutoLabelingError(f"{key}는 0~1이어야 합니다.")
    return result


def _number(value: object, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AutoLabelingError(f"{key}는 숫자여야 합니다.")
    return float(value)


def _normalized_sha256(value: str, key: str) -> str:
    normalized = value.strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", normalized) is None:
        raise AutoLabelingError(f"{key}가 SHA-256 형식이 아닙니다.")
    return normalized


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
