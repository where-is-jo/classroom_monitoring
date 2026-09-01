from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .core import load_settings, read_json
from .errors import AutoLabelingError
from .evaluation import (
    freeze_evaluation_set,
    prelabel_evaluation_set,
    sample_evaluation_frames,
)
from .partition import partition_sessions, partition_validation_extension
from .pipeline import (
    advance_local_pipeline,
    check_training_readiness,
    load_local_pipeline_config,
    load_training_pipeline_config,
    read_pipeline_status,
    run_training_pipeline,
)
from .prelabel import run_prelabel
from .prepare import prepare_run
from .preprocessing import (
    ORIGINAL_FRAME,
    UNIFORM_FULL_FRAME_PIXELATION,
    original_frame_contract,
    uniform_pixelation_contract,
)
from .privacy import export_deidentified_dataset, validate_privacy_export
from .publish import publish_dataset, validate_dataset
from .review import complete_review, create_calibration, prepare_review
from .sessionization import scan_video_folder


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m auto_labeling",
        description="승인된 MP4에서 labelImg 검수용 사람 탐지 YOLO 데이터셋을 만듭니다.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser(
        "scan-folder", help="5분 MP4 폴더 조사와 세션 manifest 생성"
    )
    scan_parser.add_argument("--input-dir", type=Path, required=True)
    scan_parser.add_argument("--output-dir", type=Path, required=True)
    scan_parser.add_argument("--timezone", default="Asia/Seoul")
    scan_parser.add_argument("--camera-id")
    scan_parser.add_argument("--camera-map", type=Path)
    scan_parser.add_argument("--metadata", type=Path)
    scan_parser.add_argument("--session-overrides", type=Path)
    scan_parser.add_argument("--expected-clip-seconds", type=float, default=300.0)
    scan_parser.add_argument("--session-gap-seconds", type=float, default=60.0)
    scan_parser.add_argument("--overlap-tolerance-seconds", type=float, default=2.0)

    partition_parser = subparsers.add_parser(
        "partition-sessions", help="세션 역할을 dataset/evaluation manifest로 고정"
    )
    partition_parser.add_argument("--scan-dir", type=Path, required=True)
    partition_parser.add_argument("--assignments", type=Path, required=True)
    partition_parser.add_argument("--output-dir", type=Path, required=True)
    partition_parser.add_argument("--allow-approved-student-data", action="store_true")

    validation_partition_parser = subparsers.add_parser(
        "partition-validation-extension",
        help="기존 비식별 train export에 추가할 val 전용 manifest 생성",
    )
    validation_partition_parser.add_argument("--scan-dir", type=Path, required=True)
    validation_partition_parser.add_argument("--assignments", type=Path, required=True)
    validation_partition_parser.add_argument("--output-dir", type=Path, required=True)
    validation_partition_parser.add_argument(
        "--base-export-dir", type=Path, required=True
    )
    validation_partition_parser.add_argument(
        "--allow-approved-student-data", action="store_true"
    )

    evaluation_parser = subparsers.add_parser(
        "sample-evaluation", help="격리된 테스트 영상에서 수동 평가 프레임 생성"
    )
    evaluation_parser.add_argument("--manifest", type=Path, required=True)
    evaluation_parser.add_argument("--output-dir", type=Path, required=True)
    evaluation_parser.add_argument("--interval-seconds", type=float, default=5.0)
    evaluation_parser.add_argument("--max-frames-per-video", type=int, default=500)
    evaluation_parser.add_argument("--target-frame-count", type=int)

    evaluation_prelabel_parser = subparsers.add_parser(
        "prelabel-evaluation", help="고정 Test에 YOLO 후보 bbox 생성"
    )
    evaluation_prelabel_parser.add_argument(
        "--evaluation-dir", type=Path, required=True
    )
    evaluation_prelabel_parser.add_argument("--model-path", type=Path, required=True)
    evaluation_prelabel_parser.add_argument("--model-sha256")
    evaluation_prelabel_parser.add_argument("--image-size", type=int)
    evaluation_prelabel_parser.add_argument("--device", default="cpu")
    evaluation_prelabel_parser.add_argument("--original-frame", action="store_true")

    freeze_parser = subparsers.add_parser(
        "freeze-evaluation", help="수동 검수 평가 세트를 해시로 동결"
    )
    freeze_parser.add_argument("--evaluation-dir", type=Path, required=True)
    freeze_parser.add_argument("--reviewer-id", required=True)
    freeze_parser.add_argument("--training-dataset-dir", type=Path, required=True)

    export_parser = subparsers.add_parser(
        "export-colab", help="머리 영역을 비식별화한 Colab 데이터셋 export"
    )
    export_parser.add_argument("--dataset-dir", type=Path, required=True)
    export_parser.add_argument("--output-dir", type=Path, required=True)
    export_parser.add_argument("--operator-id", required=True)
    export_parser.add_argument("--confirm-manual-privacy-review", action="store_true")
    export_parser.add_argument(
        "--approved-cohort-policy",
        help="수동 확인 대신 적용할 승인된 학생 집단 정책 참조",
    )
    export_parser.add_argument(
        "--preprocessing-method",
        choices=(UNIFORM_FULL_FRAME_PIXELATION, ORIGINAL_FRAME),
        default=UNIFORM_FULL_FRAME_PIXELATION,
    )

    privacy_parser = subparsers.add_parser(
        "validate-privacy", help="Colab 반출용 privacy receipt와 파일 검증"
    )
    privacy_parser.add_argument("--export-dir", type=Path, required=True)

    prepare_parser = subparsers.add_parser("prepare", help="입력 검증과 프레임 추출")
    prepare_parser.add_argument("--manifest", type=Path, required=True)
    prepare_parser.add_argument("--output-root", type=Path)
    prepare_parser.add_argument("--allow-approved-student-data", action="store_true")

    prelabel_parser = subparsers.add_parser("prelabel", help="YOLO 후보 bbox 생성")
    prelabel_parser.add_argument("--run-dir", type=Path, required=True)
    prelabel_parser.add_argument("--model-path", type=Path, required=True)
    prelabel_parser.add_argument("--model-sha256")
    prelabel_parser.add_argument("--pixelation-block-size", type=int)
    prelabel_parser.add_argument("--original-frame", action="store_true")
    prelabel_parser.add_argument("--image-size", type=int)
    prelabel_parser.add_argument("--device", default="cpu")

    review_parser = subparsers.add_parser(
        "prepare-review", help="labelImg 검수 폴더 생성"
    )
    review_parser.add_argument("--run-dir", type=Path, required=True)
    review_parser.add_argument("--batch-id", default="review-main")
    review_parser.add_argument("--calibration", type=Path, action="append", default=[])
    review_parser.add_argument("--force-full", action="store_true")

    complete_parser = subparsers.add_parser(
        "review-complete", help="검수 파일 검증과 완료 영수증 생성"
    )
    complete_parser.add_argument("--review-dir", type=Path, required=True)
    complete_parser.add_argument("--reviewer-id", required=True)
    complete_parser.add_argument("--labelimg-executable", type=Path, required=True)
    complete_parser.add_argument("--confirm-labelimg-smoke", action="store_true")

    calibrate_parser = subparsers.add_parser(
        "calibrate", help="전수 검수 결과에서 자동 승인 임계값 계산"
    )
    calibrate_parser.add_argument("--run-dir", type=Path, required=True)
    calibrate_parser.add_argument("--review-dir", type=Path, required=True)
    calibrate_parser.add_argument("--output", type=Path)

    publish_parser = subparsers.add_parser("publish", help="불변 YOLO 데이터셋 발행")
    publish_parser.add_argument("--run-dir", type=Path, required=True)
    publish_parser.add_argument("--dataset-root", type=Path)

    validate_parser = subparsers.add_parser("validate", help="발행 데이터셋 검증")
    validate_parser.add_argument("--dataset-dir", type=Path, required=True)

    pipeline_local_parser = subparsers.add_parser(
        "pipeline-local",
        help="원본 스캔부터 YOLO 자동 라벨링·학습 ZIP까지 단계 재개",
    )
    pipeline_local_parser.add_argument("--config", type=Path, required=True)
    pipeline_local_parser.add_argument(
        "--complete-review",
        action="store_true",
        help="사람 검수가 끝난 폴더를 검증하고 발행·반출까지 계속",
    )

    pipeline_status_parser = subparsers.add_parser(
        "pipeline-status", help="로컬 파이프라인의 마지막 상태 확인"
    )
    pipeline_status_parser.add_argument("--config", type=Path, required=True)

    pipeline_train_parser = subparsers.add_parser(
        "pipeline-train",
        help="비식별 ZIP 검증·smoke·YOLO11n 학습·결과 묶음 생성",
    )
    pipeline_train_parser.add_argument("--config", type=Path, required=True)
    pipeline_train_check_parser = subparsers.add_parser(
        "pipeline-train-check",
        help="학습 산출물 생성 없이 Python·CUDA·경로·입력 ZIP 준비 상태 확인",
    )
    pipeline_train_check_parser.add_argument("--config", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        if args.command == "scan-folder":
            path = scan_video_folder(
                args.input_dir,
                args.output_dir,
                timezone_name=args.timezone,
                camera_id=args.camera_id,
                camera_map_path=args.camera_map,
                metadata_path=args.metadata,
                session_overrides_path=args.session_overrides,
                expected_clip_seconds=args.expected_clip_seconds,
                session_gap_seconds=args.session_gap_seconds,
                overlap_tolerance_seconds=args.overlap_tolerance_seconds,
            )
            manifest = read_json(path / "session_manifest.json")
            _print_result(
                {
                    "scan_dir": str(path),
                    "session_count": manifest.get("session_count"),
                    "status": "folder-scanned",
                }
            )
        elif args.command == "partition-sessions":
            path = partition_sessions(
                args.scan_dir,
                args.assignments,
                args.output_dir,
                allow_approved_student_data=args.allow_approved_student_data,
            )
            _print_result(
                {"partition_dir": str(path), "status": "sessions-partitioned"}
            )
        elif args.command == "partition-validation-extension":
            path = partition_validation_extension(
                args.scan_dir,
                args.assignments,
                args.output_dir,
                args.base_export_dir,
                allow_approved_student_data=args.allow_approved_student_data,
            )
            receipt = read_json(path / "extension_receipt.json")
            _print_result(
                {
                    "partition_dir": str(path),
                    "base_train_count": receipt.get("base_train_count"),
                    "selected_source_count": receipt.get("selected_source_count"),
                    "status": "validation-extension-partitioned",
                }
            )
        elif args.command == "sample-evaluation":
            path = sample_evaluation_frames(
                args.manifest,
                args.output_dir,
                interval_seconds=args.interval_seconds,
                max_frames_per_video=args.max_frames_per_video,
                target_frame_count=args.target_frame_count,
            )
            metadata = read_json(path / "evaluation_set.json")
            _print_result(
                {
                    "evaluation_dir": str(path),
                    "frame_count": metadata.get("frame_count"),
                    "status": "evaluation-sampled",
                }
            )
        elif args.command == "prelabel-evaluation":
            path = prelabel_evaluation_set(
                args.evaluation_dir,
                args.model_path,
                settings,
                device=args.device,
                expected_model_sha256=args.model_sha256,
                image_size=args.image_size,
                input_preprocessing=(
                    original_frame_contract() if args.original_frame else None
                ),
            )
            _print_result({"receipt": str(path), "status": "evaluation-prelabeled"})
        elif args.command == "freeze-evaluation":
            path = freeze_evaluation_set(
                args.evaluation_dir,
                reviewer_id=args.reviewer_id,
                training_dataset_dir=args.training_dataset_dir,
            )
            _print_result({"receipt": str(path), "status": "evaluation-frozen"})
        elif args.command == "export-colab":
            path = export_deidentified_dataset(
                args.dataset_dir,
                args.output_dir,
                operator_id=args.operator_id,
                manual_privacy_review_confirmed=args.confirm_manual_privacy_review,
                approved_cohort_policy=args.approved_cohort_policy,
                preprocessing_method=args.preprocessing_method,
            )
            _print_result({"export_dir": str(path), "status": "colab-exported"})
        elif args.command == "validate-privacy":
            report = validate_privacy_export(args.export_dir)
            _print_result({"status": "valid", "report": report})
        elif args.command == "prepare":
            path = prepare_run(
                args.manifest,
                settings,
                output_root=args.output_root,
                allow_approved_student_data=args.allow_approved_student_data,
            )
            _print_result({"run_dir": str(path), "status": "prepared"})
        elif args.command == "prelabel":
            if args.original_frame and args.pixelation_block_size is not None:
                raise AutoLabelingError(
                    "--original-frame과 --pixelation-block-size를 함께 쓸 수 없습니다."
                )
            input_preprocessing = (
                original_frame_contract()
                if args.original_frame
                else (
                    uniform_pixelation_contract(args.pixelation_block_size)
                    if args.pixelation_block_size is not None
                    else None
                )
            )
            path = run_prelabel(
                args.run_dir,
                args.model_path,
                settings,
                device=args.device,
                expected_model_sha256=args.model_sha256,
                image_size=args.image_size,
                input_preprocessing=input_preprocessing,
            )
            _print_result({"candidate_labels": str(path), "status": "prelabeled"})
        elif args.command == "prepare-review":
            path = prepare_review(
                args.run_dir,
                settings,
                batch_id=args.batch_id,
                calibration_paths=tuple(args.calibration),
                force_full=args.force_full,
            )
            batch = read_json(path / "review-batch.json")
            _print_result(
                {
                    "review_dir": str(path),
                    "review_frame_count": len(batch.get("frame_ids", [])),
                    "auto_accepted_frame_count": len(
                        batch.get("auto_accepted_frame_ids", [])
                    ),
                    "status": "review-prepared",
                }
            )
        elif args.command == "review-complete":
            path = complete_review(
                args.review_dir,
                args.reviewer_id,
                settings,
                labelimg_executable=args.labelimg_executable,
                labelimg_smoke_confirmed=args.confirm_labelimg_smoke,
            )
            receipt = read_json(path)
            _print_result(
                {
                    "receipt": str(path),
                    "quality_gate": receipt.get("quality_gate"),
                    "status": "review-completed",
                }
            )
        elif args.command == "calibrate":
            path = create_calibration(
                args.run_dir,
                args.review_dir,
                settings,
                output_path=args.output,
            )
            _print_result({"calibration": str(path), "status": "calibrated"})
        elif args.command == "publish":
            path = publish_dataset(
                args.run_dir, dataset_root=args.dataset_root, settings=settings
            )
            _print_result({"dataset_dir": str(path), "status": "published"})
        elif args.command == "validate":
            report = validate_dataset(args.dataset_dir)
            _print_result({"status": "valid", "report": report})
        elif args.command == "pipeline-local":
            local_config = load_local_pipeline_config(args.config)
            _print_result(
                advance_local_pipeline(
                    local_config,
                    complete_review_now=args.complete_review,
                )
            )
        elif args.command == "pipeline-status":
            local_config = load_local_pipeline_config(args.config)
            _print_result(read_pipeline_status(local_config))
        elif args.command == "pipeline-train":
            training_config = load_training_pipeline_config(args.config)
            _print_result(run_training_pipeline(training_config))
        elif args.command == "pipeline-train-check":
            training_config = load_training_pipeline_config(args.config)
            report = check_training_readiness(training_config)
            _print_result(report)
            return 0 if report.get("status") == "ready-for-training" else 2
        else:
            parser.error("알 수 없는 명령입니다.")
    except AutoLabelingError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    except OSError:
        print("오류: 파일 작업을 완료할 수 없습니다.", file=sys.stderr)
        return 2
    return 0


def _print_result(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
