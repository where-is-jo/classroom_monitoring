from dataclasses import dataclass
from enum import Enum

import pytest

from deeplearning.realtime_diagnostics import RealtimeFaceDiagnostics


class Status(str, Enum):
    REGISTERED = "registered"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Detection:
    bbox: tuple[int, int, int, int]
    detection_confidence: float
    similarity: float
    margin: float
    quality: float
    status: Status


def test_검출_누락과_식별_결과를_분리해_집계한다() -> None:
    diagnostics = RealtimeFaceDiagnostics()
    diagnostics.record(
        expected_faces=2,
        detections=(Detection((0, 0, 80, 100), 0.8, 0.7, 0.2, 0.6, Status.REGISTERED),),
        recognition_performed=True,
    )
    diagnostics.record(
        expected_faces=2,
        detections=(
            Detection((0, 0, 40, 50), 0.6, -1.0, -1.0, 0.3, Status.UNKNOWN),
            Detection((0, 0, 60, 90), 0.7, -1.0, -1.0, 0.5, Status.UNKNOWN),
        ),
        recognition_performed=False,
    )

    result = diagnostics.snapshot()

    assert result["detection_cycles"] == 2
    assert result["expected_face_total"] == 4
    assert result["detected_face_total"] == 3
    assert result["missed_face_total"] == 1
    assert result["estimated_miss_rate"] == pytest.approx(0.25)
    assert result["average_face_size_pixels"] == pytest.approx(60.0)
    assert result["average_detection_confidence"] == pytest.approx(0.7)
    assert result["recognition_samples"] == 1
    assert result["status_counts"] == {"registered": 1}


def test_기대_얼굴_수를_입력하지_않으면_누락률은_계산하지_않는다() -> None:
    diagnostics = RealtimeFaceDiagnostics()

    diagnostics.record(expected_faces=None, detections=(), recognition_performed=True)

    assert diagnostics.snapshot()["estimated_miss_rate"] is None


def test_음수_기대_얼굴_수는_거부한다() -> None:
    diagnostics = RealtimeFaceDiagnostics()

    with pytest.raises(ValueError, match="0 이상"):
        diagnostics.record(expected_faces=-1, detections=(), recognition_performed=True)
