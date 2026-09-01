"""ArcFace와 AdaFace 비식별 평가 요약이 cutover 정확도 조건을 만족하는지 검증한다."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from deeplearning.training.face_identification_eval import (
    MINIMUM_DIFFERENT_IDENTITY_PAIRS,
    MINIMUM_UNKNOWN_PROBES,
)

ModelName = Literal["arcface", "adaface"]
MAXIMUM_TARGET_FAR = 0.001


@dataclass(frozen=True)
class ModelEvaluationSummary:
    model_name: ModelName
    target_far: float
    track_target_false_association: float
    registered_success_rate: float
    unknown_false_accept_rate: float
    track_different_identity_false_association_rate: float
    registered_probe_count: int
    unknown_probe_count: int
    track_different_identity_pair_count: int


@dataclass(frozen=True)
class ComparisonResult:
    target_far: float
    arcface_registered_success_rate: float
    adaface_registered_success_rate: float
    adaface_unknown_false_accept_rate: float


def _required_number(value: dict[str, Any], key: str) -> float:
    raw = value.get(key)
    if (
        not isinstance(raw, (int, float))
        or isinstance(raw, bool)
        or not math.isfinite(float(raw))
    ):
        raise ValueError(f"평가 요약의 {key} 값이 올바르지 않습니다.")
    return float(raw)


def _required_count(value: dict[str, Any], key: str) -> int:
    raw = value.get(key)
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
        raise ValueError(f"평가 요약의 {key} 값이 올바르지 않습니다.")
    return raw


def load_summary(path: Path, *, expected_model: ModelName) -> ModelEvaluationSummary:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("얼굴 모델 평가 요약을 읽을 수 없습니다.") from error
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("얼굴 모델 평가 요약 schema_version은 1이어야 합니다.")
    if value.get("model_name") != expected_model:
        raise ValueError("얼굴 모델 평가 요약의 모델명이 예상 모델과 다릅니다.")
    return ModelEvaluationSummary(
        model_name=expected_model,
        target_far=_required_number(value, "target_far"),
        track_target_false_association=_required_number(
            value, "track_target_false_association"
        ),
        registered_success_rate=_required_number(value, "registered_success_rate"),
        unknown_false_accept_rate=_required_number(value, "unknown_false_accept_rate"),
        track_different_identity_false_association_rate=_required_number(
            value, "track_different_identity_false_association_rate"
        ),
        registered_probe_count=_required_count(value, "registered_probe_count"),
        unknown_probe_count=_required_count(value, "unknown_probe_count"),
        track_different_identity_pair_count=_required_count(
            value, "track_different_identity_pair_count"
        ),
    )


def _validate_summary(summary: ModelEvaluationSummary) -> None:
    for name, value in (
        ("target FAR", summary.target_far),
        ("track 목표 오연결률", summary.track_target_false_association),
        ("known 성공률", summary.registered_success_rate),
        ("unknown FAR", summary.unknown_false_accept_rate),
        (
            "track 실제 오연결률",
            summary.track_different_identity_false_association_rate,
        ),
    ):
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{summary.model_name} {name} 범위가 올바르지 않습니다.")
    if summary.target_far > MAXIMUM_TARGET_FAR:
        raise ValueError(f"{summary.model_name} target FAR이 0.001을 초과합니다.")
    if summary.track_target_false_association > MAXIMUM_TARGET_FAR:
        raise ValueError(
            f"{summary.model_name} track 목표 오연결률이 0.001을 초과합니다."
        )
    if summary.unknown_probe_count < MINIMUM_UNKNOWN_PROBES:
        raise ValueError(
            f"{summary.model_name} unknown test probe가 1,000개 미만입니다."
        )
    if summary.track_different_identity_pair_count < MINIMUM_DIFFERENT_IDENTITY_PAIRS:
        raise ValueError(
            f"{summary.model_name} 다른 사람 track 쌍이 1,000쌍 미만입니다."
        )
    if summary.registered_probe_count <= 0:
        raise ValueError(f"{summary.model_name} known test probe가 비어 있습니다.")
    if summary.unknown_false_accept_rate > summary.target_far:
        raise ValueError(f"{summary.model_name} test FAR이 목표 FAR을 초과합니다.")
    if (
        summary.track_different_identity_false_association_rate
        > summary.track_target_false_association
    ):
        raise ValueError(
            f"{summary.model_name} track 실제 오연결률이 목표를 초과합니다."
        )


def verify_model_comparison(
    *, arcface_summary_path: Path, adaface_summary_path: Path
) -> ComparisonResult:
    """두 모델을 같은 FAR에서 비교하고 AdaFace 비열화 여부를 확인한다."""

    arcface = load_summary(arcface_summary_path, expected_model="arcface")
    adaface = load_summary(adaface_summary_path, expected_model="adaface")
    _validate_summary(arcface)
    _validate_summary(adaface)
    if not math.isclose(
        arcface.target_far, adaface.target_far, rel_tol=0.0, abs_tol=1e-12
    ):
        raise ValueError("ArcFace와 AdaFace 평가의 target FAR이 다릅니다.")
    if adaface.registered_success_rate < arcface.registered_success_rate:
        raise ValueError("AdaFace known 성공률이 같은 FAR의 ArcFace보다 낮습니다.")
    return ComparisonResult(
        target_far=adaface.target_far,
        arcface_registered_success_rate=arcface.registered_success_rate,
        adaface_registered_success_rate=adaface.registered_success_rate,
        adaface_unknown_false_accept_rate=adaface.unknown_false_accept_rate,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arcface-summary", required=True, type=Path)
    parser.add_argument("--adaface-summary", required=True, type=Path)
    args = parser.parse_args(argv)

    result = verify_model_comparison(
        arcface_summary_path=args.arcface_summary,
        adaface_summary_path=args.adaface_summary,
    )
    print(
        "얼굴 모델 비교 게이트 통과: "
        f"target_far={result.target_far:.4f}, "
        f"ArcFace_known={result.arcface_registered_success_rate:.4f}, "
        f"AdaFace_known={result.adaface_registered_success_rate:.4f}, "
        f"AdaFace_test_far={result.adaface_unknown_false_accept_rate:.4f}"
    )
    print("학생 식별자·이미지 경로·개별 점수는 출력하지 않았습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
