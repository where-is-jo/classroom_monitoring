from __future__ import annotations

import json
from pathlib import Path

import pytest

from deeplearning.training.verify_face_model_comparison import (
    verify_model_comparison,
)


def _summary(
    path: Path,
    *,
    model_name: str,
    known_success: float,
    test_far: float = 0.001,
    target_far: float = 0.001,
    unknown_count: int = 1_000,
    track_pairs: int = 1_000,
    track_far: float = 0.001,
) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model_name": model_name,
                "target_far": target_far,
                "track_target_false_association": 0.001,
                "registered_success_rate": known_success,
                "unknown_false_accept_rate": test_far,
                "track_different_identity_false_association_rate": track_far,
                "registered_probe_count": 300,
                "unknown_probe_count": unknown_count,
                "track_different_identity_pair_count": track_pairs,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_AdaFace가_같은_FAR에서_비열화되지_않으면_통과한다(tmp_path: Path) -> None:
    result = verify_model_comparison(
        arcface_summary_path=_summary(
            tmp_path / "arcface.json", model_name="arcface", known_success=0.91
        ),
        adaface_summary_path=_summary(
            tmp_path / "adaface.json", model_name="adaface", known_success=0.92
        ),
    )

    assert result.adaface_registered_success_rate == 0.92
    assert result.adaface_unknown_false_accept_rate == 0.001


def test_AdaFace_known_성공률이_ArcFace보다_낮으면_실패한다(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="known 성공률"):
        verify_model_comparison(
            arcface_summary_path=_summary(
                tmp_path / "arcface.json", model_name="arcface", known_success=0.91
            ),
            adaface_summary_path=_summary(
                tmp_path / "adaface.json", model_name="adaface", known_success=0.90
            ),
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"test_far": 0.002}, "test FAR"),
        ({"unknown_count": 999}, "unknown test probe"),
        ({"track_pairs": 999}, "track 쌍"),
        ({"track_far": 0.002}, "track 실제 오연결률"),
    ],
)
def test_AdaFace_안전_표본과_오인식_게이트를_강제한다(
    tmp_path: Path, overrides: dict[str, float | int], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        verify_model_comparison(
            arcface_summary_path=_summary(
                tmp_path / "arcface.json", model_name="arcface", known_success=0.91
            ),
            adaface_summary_path=_summary(
                tmp_path / "adaface.json",
                model_name="adaface",
                known_success=0.92,
                **overrides,
            ),
        )


def test_두_모델_target_FAR이_다르면_비교하지_않는다(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="target FAR이 다릅니다"):
        verify_model_comparison(
            arcface_summary_path=_summary(
                tmp_path / "arcface.json",
                model_name="arcface",
                known_success=0.91,
                target_far=0.0005,
                test_far=0.0005,
            ),
            adaface_summary_path=_summary(
                tmp_path / "adaface.json", model_name="adaface", known_success=0.92
            ),
        )
