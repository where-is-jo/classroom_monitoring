from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .core import load_settings, read_json
from .errors import AutoLabelingError
from .prelabel import run_prelabel
from .prepare import prepare_run
from .publish import publish_dataset, validate_dataset
from .review import complete_review, create_calibration, prepare_review


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m auto_labeling",
        description="승인된 MP4에서 labelImg 검수용 사람 탐지 YOLO 데이터셋을 만듭니다.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare", help="입력 검증과 프레임 추출")
    prepare_parser.add_argument("--manifest", type=Path, required=True)
    prepare_parser.add_argument("--output-root", type=Path)

    prelabel_parser = subparsers.add_parser("prelabel", help="YOLO 후보 bbox 생성")
    prelabel_parser.add_argument("--run-dir", type=Path, required=True)
    prelabel_parser.add_argument("--model-path", type=Path, required=True)
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        if args.command == "prepare":
            path = prepare_run(args.manifest, settings, output_root=args.output_root)
            _print_result({"run_dir": str(path), "status": "prepared"})
        elif args.command == "prelabel":
            path = run_prelabel(
                args.run_dir, args.model_path, settings, device=args.device
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
