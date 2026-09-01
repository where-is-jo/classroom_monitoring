"""실시간 얼굴 검출·식별 실패를 개인정보 없이 집계한다."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol


class DiagnosticDetection(Protocol):
    bbox: tuple[int, int, int, int]
    detection_confidence: float
    similarity: float
    margin: float
    quality: float
    status: object


@dataclass
class RealtimeFaceDiagnostics:
    """수동 기대 얼굴 수와 모델 결과를 검출 주기 단위로 비교한다."""

    detection_cycles: int = 0
    expected_face_total: int = 0
    detected_face_total: int = 0
    missed_face_total: int = 0
    face_size_total_pixels: int = 0
    detection_confidence_total: float = 0.0
    quality_total: float = 0.0
    recognition_samples: int = 0
    similarity_total: float = 0.0
    margin_total: float = 0.0
    status_counts: Counter[str] = field(default_factory=Counter)

    def record(
        self,
        *,
        expected_faces: int | None,
        detections: Sequence[DiagnosticDetection],
        recognition_performed: bool,
    ) -> None:
        if expected_faces is not None and expected_faces < 0:
            raise ValueError("기대 얼굴 수는 0 이상이어야 합니다.")

        detected_faces = len(detections)
        self.detection_cycles += 1
        self.detected_face_total += detected_faces
        if expected_faces is not None:
            self.expected_face_total += expected_faces
            self.missed_face_total += max(0, expected_faces - detected_faces)

        for detection in detections:
            left, top, right, bottom = detection.bbox
            self.face_size_total_pixels += max(0, min(right - left, bottom - top))
            self.detection_confidence_total += detection.detection_confidence
            self.quality_total += detection.quality
            if recognition_performed:
                self.recognition_samples += 1
                self.similarity_total += detection.similarity
                self.margin_total += detection.margin
                status = getattr(detection.status, "value", str(detection.status))
                self.status_counts[str(status)] += 1

    def snapshot(self) -> dict[str, object]:
        detected = self.detected_face_total
        expected = self.expected_face_total
        recognized = self.recognition_samples
        return {
            "detection_cycles": self.detection_cycles,
            "expected_face_total": expected,
            "detected_face_total": detected,
            "missed_face_total": self.missed_face_total,
            "estimated_miss_rate": self.missed_face_total / expected
            if expected
            else None,
            "average_face_size_pixels": self.face_size_total_pixels / detected
            if detected
            else None,
            "average_detection_confidence": self.detection_confidence_total / detected
            if detected
            else None,
            "average_quality": self.quality_total / detected if detected else None,
            "recognition_samples": recognized,
            "average_similarity": self.similarity_total / recognized
            if recognized
            else None,
            "average_margin": self.margin_total / recognized if recognized else None,
            "status_counts": dict(sorted(self.status_counts.items())),
        }
