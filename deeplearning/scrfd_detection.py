"""SCRFD 얼굴 후보 검출과 시간축 bbox 유지를 담당한다."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any

import numpy as np


class FaceCandidateStatus(str, Enum):
    FACE = "face"
    REVIEW = "review"


@dataclass(frozen=True)
class ScrfdDetection:
    bbox: tuple[int, int, int, int]
    confidence: float
    status: FaceCandidateStatus


@dataclass(frozen=True)
class TrackedScrfdDetection:
    track_id: int
    bbox: tuple[int, int, int, int]
    confidence: float
    status: FaceCandidateStatus
    missed_cycles: int


def _area(bbox: tuple[int, int, int, int]) -> int:
    return max(0, bbox[2] - bbox[0]) * max(0, bbox[3] - bbox[1])


def _iou(
    left_bbox: tuple[int, int, int, int],
    right_bbox: tuple[int, int, int, int],
) -> float:
    left = max(left_bbox[0], right_bbox[0])
    top = max(left_bbox[1], right_bbox[1])
    right = min(left_bbox[2], right_bbox[2])
    bottom = min(left_bbox[3], right_bbox[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    if intersection == 0:
        return 0.0
    return intersection / max(1, _area(left_bbox) + _area(right_bbox) - intersection)


def _intersection_over_smaller(
    left_bbox: tuple[int, int, int, int],
    right_bbox: tuple[int, int, int, int],
) -> float:
    left = max(left_bbox[0], right_bbox[0])
    top = max(left_bbox[1], right_bbox[1])
    right = min(left_bbox[2], right_bbox[2])
    bottom = min(left_bbox[3], right_bbox[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    return intersection / max(1, min(_area(left_bbox), _area(right_bbox)))


def _has_face_like_landmarks(
    landmarks: np.ndarray,
    bbox: tuple[int, int, int, int],
) -> bool:
    """고각도·옆면을 허용하면서 명백히 무너진 5점 landmark만 거부한다."""
    points = np.asarray(landmarks, dtype=np.float32)
    if points.shape != (5, 2) or not np.isfinite(points).all():
        return False

    left, top, right, bottom = bbox
    width = max(1, right - left)
    height = max(1, bottom - top)
    margin_x = width * 0.2
    margin_y = height * 0.2
    if (
        (points[:, 0] < left - margin_x).any()
        or (points[:, 0] > right + margin_x).any()
        or (points[:, 1] < top - margin_y).any()
        or (points[:, 1] > bottom + margin_y).any()
    ):
        return False

    eye_distance = float(np.linalg.norm(points[0] - points[1]))
    mouth_distance = float(np.linalg.norm(points[3] - points[4]))
    vertical_span = float(points[:, 1].max() - points[:, 1].min())
    if eye_distance < width * 0.04 or mouth_distance < width * 0.025:
        return False
    if vertical_span < height * 0.12:
        return False

    eye_y = float((points[0, 1] + points[1, 1]) / 2)
    nose_y = float(points[2, 1])
    mouth_y = float((points[3, 1] + points[4, 1]) / 2)
    tolerance = height * 0.18
    return eye_y <= nose_y + tolerance and nose_y <= mouth_y + tolerance


class ScrfdCandidateDetector:
    """낮은 confidence 후보까지 유지하는 SCRFD 검출 어댑터."""

    def __init__(
        self,
        model: Any,
        *,
        candidate_threshold: float = 0.25,
        face_threshold: float = 0.6,
    ) -> None:
        if not 0.0 <= candidate_threshold < face_threshold <= 1.0:
            raise ValueError("candidate threshold는 face threshold보다 작아야 합니다.")
        self._model = model
        self._candidate_threshold = candidate_threshold
        self._face_threshold = face_threshold

    def detect(self, image_bgr: np.ndarray) -> tuple[ScrfdDetection, ...]:
        if image_bgr is None or image_bgr.size == 0:
            return ()
        raw_detections, raw_landmarks = self._model.detect(image_bgr, max_num=0)
        height, width = image_bgr.shape[:2]
        results: list[ScrfdDetection] = []
        if raw_landmarks is None:
            return ()
        for raw, landmarks in zip(raw_detections, raw_landmarks):
            confidence = float(raw[4])
            if confidence < self._candidate_threshold:
                continue
            left, top, right, bottom = (round(value) for value in raw[:4])
            bbox = (
                max(0, left),
                max(0, top),
                min(width - 1, right),
                min(height - 1, bottom),
            )
            if _area(bbox) == 0:
                continue
            if not _has_face_like_landmarks(landmarks, bbox):
                continue
            status = (
                FaceCandidateStatus.FACE
                if confidence >= self._face_threshold
                else FaceCandidateStatus.REVIEW
            )
            results.append(ScrfdDetection(bbox, confidence, status))
        return tuple(results)

    def detect_tiled(
        self,
        image_bgr: np.ndarray,
        *,
        rows: int = 2,
        columns: int = 2,
        overlap: float = 0.2,
        include_full_frame: bool = True,
        nms_iou_threshold: float = 0.35,
        containment_threshold: float = 0.8,
    ) -> tuple[ScrfdDetection, ...]:
        if rows < 1 or columns < 1:
            raise ValueError("타일 행과 열은 1 이상이어야 합니다.")
        if not 0.0 <= overlap < 0.5:
            raise ValueError("타일 겹침 비율은 0 이상 0.5 미만이어야 합니다.")

        height, width = image_bgr.shape[:2]
        candidates = list(self.detect(image_bgr)) if include_full_frame else []
        tile_height = math.ceil(height / rows)
        tile_width = math.ceil(width / columns)
        y_padding = round(tile_height * overlap)
        x_padding = round(tile_width * overlap)

        for row in range(rows):
            for column in range(columns):
                left = max(0, column * tile_width - x_padding)
                top = max(0, row * tile_height - y_padding)
                right = min(width, (column + 1) * tile_width + x_padding)
                bottom = min(height, (row + 1) * tile_height + y_padding)
                tile = image_bgr[top:bottom, left:right]
                for detection in self.detect(tile):
                    tile_left, tile_top, tile_right, tile_bottom = detection.bbox
                    candidates.append(
                        ScrfdDetection(
                            (
                                tile_left + left,
                                tile_top + top,
                                tile_right + left,
                                tile_bottom + top,
                            ),
                            detection.confidence,
                            detection.status,
                        )
                    )

        kept: list[ScrfdDetection] = []
        for candidate in sorted(
            candidates,
            key=lambda item: (item.confidence, _area(item.bbox)),
            reverse=True,
        ):
            duplicate = any(
                _iou(candidate.bbox, existing.bbox) >= nms_iou_threshold
                or _intersection_over_smaller(candidate.bbox, existing.bbox)
                >= containment_threshold
                for existing in kept
            )
            if not duplicate:
                kept.append(candidate)
        return tuple(kept)


@dataclass
class _Track:
    bbox: tuple[int, int, int, int]
    confidence: float
    status: FaceCandidateStatus
    missed_cycles: int = 0


class ScrfdBoxTracker:
    """검출 사이의 짧은 누락에도 bbox를 유지해 깜빡임을 줄인다."""

    def __init__(self, *, stale_cycles: int = 3, smoothing: float = 0.65) -> None:
        if stale_cycles < 0:
            raise ValueError("stale cycles는 0 이상이어야 합니다.")
        if not 0.0 < smoothing <= 1.0:
            raise ValueError("smoothing은 0 초과 1 이하여야 합니다.")
        self._stale_cycles = stale_cycles
        self._smoothing = smoothing
        self._next_track_id = 1
        self._tracks: dict[int, _Track] = {}

    def _match(self, detection: ScrfdDetection, available: set[int]) -> int | None:
        detection_center = (
            (detection.bbox[0] + detection.bbox[2]) / 2,
            (detection.bbox[1] + detection.bbox[3]) / 2,
        )
        best: tuple[float, int] | None = None
        for track_id in available:
            track = self._tracks[track_id]
            overlap = _iou(detection.bbox, track.bbox)
            track_center = (
                (track.bbox[0] + track.bbox[2]) / 2,
                (track.bbox[1] + track.bbox[3]) / 2,
            )
            distance = math.dist(detection_center, track_center)
            face_size = max(
                20, min(track.bbox[2] - track.bbox[0], track.bbox[3] - track.bbox[1])
            )
            if overlap < 0.05 and distance > max(60, face_size * 1.5):
                continue
            score = overlap - distance / max(1, face_size * 10)
            if best is None or score > best[0]:
                best = (score, track_id)
        return best[1] if best else None

    def update(
        self, detections: tuple[ScrfdDetection, ...]
    ) -> tuple[TrackedScrfdDetection, ...]:
        available = set(self._tracks)
        seen: set[int] = set()
        for detection in detections:
            track_id = self._match(detection, available)
            if track_id is None:
                track_id = self._next_track_id
                self._next_track_id += 1
                self._tracks[track_id] = _Track(
                    detection.bbox, detection.confidence, detection.status
                )
            else:
                available.remove(track_id)
                track = self._tracks[track_id]
                track.bbox = tuple(
                    round((1.0 - self._smoothing) * old + self._smoothing * new)
                    for old, new in zip(track.bbox, detection.bbox)
                )
                track.confidence = detection.confidence
                track.status = detection.status
                track.missed_cycles = 0
            seen.add(track_id)

        for track_id, track in self._tracks.items():
            if track_id not in seen:
                track.missed_cycles += 1
        self._tracks = {
            track_id: track
            for track_id, track in self._tracks.items()
            if track.missed_cycles <= self._stale_cycles
        }
        return tuple(
            TrackedScrfdDetection(
                track_id,
                track.bbox,
                track.confidence,
                track.status,
                track.missed_cycles,
            )
            for track_id, track in sorted(self._tracks.items())
        )
