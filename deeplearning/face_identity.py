"""SCRFD와 얼굴 임베딩 모델을 이용한 오픈셋 다중 얼굴 식별."""

from __future__ import annotations

import math
import time
from collections import Counter, deque
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Sequence

import cv2
import numpy as np
from insightface.utils import face_align

EMBEDDING_DIMENSION = 512


class IdentityStatus(str, Enum):
    REGISTERED = "registered"
    UNKNOWN = "unknown"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class GalleryEntry:
    student_id: str
    vector: np.ndarray


@dataclass(frozen=True)
class FaceGallery:
    entries: tuple[GalleryEntry, ...]
    matrix: np.ndarray

    @classmethod
    def from_entries(cls, entries: Sequence[GalleryEntry]) -> "FaceGallery":
        if not entries:
            raise ValueError("gallery가 비어 있습니다.")

        student_ids = [entry.student_id for entry in entries]
        if any(not value for value in student_ids) or len(student_ids) != len(set(student_ids)):
            raise ValueError("student_id는 비어 있거나 중복될 수 없습니다.")

        normalized = tuple(
            GalleryEntry(entry.student_id, normalize_embedding(entry.vector))
            for entry in entries
        )
        matrix = np.stack([entry.vector for entry in normalized]).astype(np.float32)
        return cls(normalized, matrix)


@dataclass(frozen=True)
class IdentityThresholds:
    similarity: float
    margin: float

    def __post_init__(self) -> None:
        if not -1.0 <= self.similarity <= 1.0:
            raise ValueError("similarity threshold는 -1과 1 사이여야 합니다.")
        if not 0.0 <= self.margin <= 2.0:
            raise ValueError("margin threshold는 0과 2 사이여야 합니다.")


@dataclass(frozen=True)
class FaceQuality:
    score: float
    face_size: int
    blur_score: float
    brightness: float
    detection_confidence: float


@dataclass(frozen=True)
class FaceIdentityDetection:
    bbox: tuple[int, int, int, int]
    detection_confidence: float
    student_id: str | None
    similarity: float
    margin: float
    quality: float = 0.0
    rejected_reason: str | None = None
    status: IdentityStatus = IdentityStatus.UNCERTAIN
    embedding: np.ndarray | None = field(default=None, repr=False, compare=False)
    tta_used: bool = False


@dataclass(frozen=True)
class TrackedIdentity:
    track_id: int
    bbox: tuple[int, int, int, int]
    status: IdentityStatus
    student_id: str | None
    similarity: float
    margin: float
    quality: float
    observation_count: int


def normalize_embedding(value: Any) -> np.ndarray:
    vector = np.asarray(value, dtype=np.float32).reshape(-1)
    if vector.size != EMBEDDING_DIMENSION or not np.isfinite(vector).all():
        raise ValueError("embedding은 유한한 512차원 벡터여야 합니다.")

    norm = float(np.linalg.norm(vector))
    if norm <= 1e-12:
        raise ValueError("embedding norm이 0입니다.")
    return vector / norm


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _bbox_center(bbox: tuple[int, int, int, int]) -> tuple[float, float]:
    left, top, right, bottom = bbox
    return ((left + right) / 2.0, (top + bottom) / 2.0)


def _bbox_iou(
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

    left_area = max(0, left_bbox[2] - left_bbox[0]) * max(
        0, left_bbox[3] - left_bbox[1]
    )
    right_area = max(0, right_bbox[2] - right_bbox[0]) * max(
        0, right_bbox[3] - right_bbox[1]
    )
    return intersection / max(1, left_area + right_area - intersection)


def _bbox_intersection_over_smaller(
    left_bbox: tuple[int, int, int, int],
    right_bbox: tuple[int, int, int, int],
) -> float:
    """두 박스의 교집합이 더 작은 박스를 얼마나 덮는지 반환한다."""
    left = max(left_bbox[0], right_bbox[0])
    top = max(left_bbox[1], right_bbox[1])
    right = min(left_bbox[2], right_bbox[2])
    bottom = min(left_bbox[3], right_bbox[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    if intersection == 0:
        return 0.0

    left_area = max(0, left_bbox[2] - left_bbox[0]) * max(
        0, left_bbox[3] - left_bbox[1]
    )
    right_area = max(0, right_bbox[2] - right_bbox[0]) * max(
        0, right_bbox[3] - right_bbox[1]
    )
    return intersection / max(1, min(left_area, right_area))


def _bbox_area(bbox: tuple[int, int, int, int]) -> int:
    return max(0, bbox[2] - bbox[0]) * max(0, bbox[3] - bbox[1])


class FaceIdentityEngine:
    """한 프레임에서 모든 얼굴을 검출하고 3상태로 식별한다."""

    def __init__(
        self,
        *,
        detector: Any,
        recognizer: Any,
        gallery: FaceGallery,
        thresholds: IdentityThresholds,
        detection_threshold: float = 0.6,
        minimum_face_size: int = 40,
        preferred_face_size: int = 112,
        minimum_blur_score: float = 20.0,
        preferred_blur_score: float = 100.0,
        uncertain_quality_threshold: float = 0.45,
        use_flip_tta: bool = True,
        tta_similarity_band: float = 0.08,
        tta_margin_band: float = 0.06,
    ) -> None:
        if not 0.0 <= detection_threshold <= 1.0:
            raise ValueError("detection threshold는 0과 1 사이여야 합니다.")
        if not 0.0 <= uncertain_quality_threshold <= 1.0:
            raise ValueError("품질 임계값은 0과 1 사이여야 합니다.")
        if minimum_face_size < 1 or preferred_face_size <= minimum_face_size:
            raise ValueError("선호 얼굴 크기는 최소 얼굴 크기보다 커야 합니다.")
        if minimum_blur_score < 0 or preferred_blur_score <= minimum_blur_score:
            raise ValueError("선호 선명도는 최소 선명도보다 커야 합니다.")

        self._detector = detector
        self._recognizer = recognizer
        self._gallery = gallery
        self._thresholds = thresholds
        self._detection_threshold = detection_threshold
        self._minimum_face_size = minimum_face_size
        self._preferred_face_size = preferred_face_size
        self._minimum_blur_score = minimum_blur_score
        self._preferred_blur_score = preferred_blur_score
        self._uncertain_quality_threshold = uncertain_quality_threshold
        self._use_flip_tta = use_flip_tta
        self._tta_similarity_band = tta_similarity_band
        self._tta_margin_band = tta_margin_band
        self._last_timings_ms = {"detector": 0.0, "recognizer": 0.0}

    @property
    def last_timings_ms(self) -> dict[str, float]:
        """마지막 호출에서 detector와 recognizer가 사용한 시간을 반환한다."""
        return self._last_timings_ms.copy()

    def _quality(
        self,
        aligned: np.ndarray,
        face_size: int,
        detection_confidence: float,
    ) -> FaceQuality:
        gray = cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY)
        blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        brightness = float(gray.mean())

        size_score = _clamp01(
            (face_size - self._minimum_face_size)
            / (self._preferred_face_size - self._minimum_face_size)
        )
        blur_component = _clamp01(
            (blur_score - self._minimum_blur_score)
            / (self._preferred_blur_score - self._minimum_blur_score)
        )
        brightness_component = _clamp01(1.0 - abs(brightness - 127.5) / 127.5)
        detection_component = _clamp01(
            (detection_confidence - self._detection_threshold)
            / max(1e-6, 1.0 - self._detection_threshold)
        )
        score = (
            0.35 * size_score
            + 0.35 * blur_component
            + 0.15 * brightness_component
            + 0.15 * detection_component
        )
        return FaceQuality(
            score=_clamp01(score),
            face_size=face_size,
            blur_score=blur_score,
            brightness=brightness,
            detection_confidence=detection_confidence,
        )

    def _scores(self, embedding: np.ndarray) -> tuple[int, float, float]:
        scores = self._gallery.matrix @ normalize_embedding(embedding)
        order = np.argsort(scores)[::-1]
        top_index = int(order[0])
        similarity = float(scores[top_index])
        second = float(scores[int(order[1])]) if len(order) > 1 else -1.0
        return top_index, similarity, similarity - second

    def _needs_tta(self, similarity: float, margin: float) -> bool:
        similarity_near = abs(similarity - self._thresholds.similarity) <= self._tta_similarity_band
        margin_near = abs(margin - self._thresholds.margin) <= self._tta_margin_band
        return self._use_flip_tta and (similarity_near or margin_near)

    def match_embedding(
        self,
        embedding: np.ndarray,
        *,
        quality: float = 1.0,
    ) -> tuple[IdentityStatus, str | None, float, float, str | None]:
        top_index, similarity, margin = self._scores(embedding)
        if quality < self._uncertain_quality_threshold:
            return IdentityStatus.UNCERTAIN, None, similarity, margin, "low_quality"

        if (
            similarity >= self._thresholds.similarity
            and margin >= self._thresholds.margin
        ):
            return (
                IdentityStatus.REGISTERED,
                self._gallery.entries[top_index].student_id,
                similarity,
                margin,
                None,
            )
        return IdentityStatus.UNKNOWN, None, similarity, margin, "open_set_threshold"

    def identify(
        self,
        image_bgr: np.ndarray,
        *,
        extract_embeddings: bool = True,
    ) -> tuple[FaceIdentityDetection, ...]:
        if image_bgr is None or image_bgr.size == 0:
            self._last_timings_ms = {"detector": 0.0, "recognizer": 0.0}
            return ()

        detector_started = time.perf_counter()
        detections, keypoints = self._detector.detect(image_bgr, max_num=0)
        detector_ms = (time.perf_counter() - detector_started) * 1000.0
        recognizer_ms = 0.0
        if keypoints is None:
            self._last_timings_ms = {
                "detector": detector_ms,
                "recognizer": recognizer_ms,
            }
            return ()

        height, width = image_bgr.shape[:2]
        results: list[FaceIdentityDetection] = []
        for detection, landmark in zip(detections, keypoints):
            confidence = float(detection[4])
            if confidence < self._detection_threshold:
                continue

            left, top, right, bottom = (int(round(value)) for value in detection[:4])
            bbox = (
                max(0, left),
                max(0, top),
                min(width - 1, right),
                min(height - 1, bottom),
            )
            aligned = face_align.norm_crop(
                image_bgr,
                landmark=landmark,
                image_size=112,
            )
            face_size = min(bbox[2] - bbox[0], bbox[3] - bbox[1])
            face_quality = self._quality(aligned, face_size, confidence)

            if not extract_embeddings:
                results.append(
                    FaceIdentityDetection(
                        bbox=bbox,
                        detection_confidence=confidence,
                        student_id=None,
                        similarity=-1.0,
                        margin=-1.0,
                        quality=face_quality.score,
                        rejected_reason="identity_not_scheduled",
                        status=IdentityStatus.UNCERTAIN,
                        embedding=None,
                        tta_used=False,
                    )
                )
                continue

            recognizer_started = time.perf_counter()
            embedding = normalize_embedding(self._recognizer.get_feat(aligned))
            _, similarity, margin = self._scores(embedding)
            tta_used = self._needs_tta(similarity, margin)
            if tta_used:
                flipped = normalize_embedding(
                    self._recognizer.get_feat(cv2.flip(aligned, 1))
                )
                embedding = normalize_embedding(embedding + flipped)
            recognizer_ms += (time.perf_counter() - recognizer_started) * 1000.0

            status, student_id, similarity, margin, reason = self.match_embedding(
                embedding,
                quality=face_quality.score,
            )
            results.append(
                FaceIdentityDetection(
                    bbox=bbox,
                    detection_confidence=confidence,
                    student_id=student_id,
                    similarity=similarity,
                    margin=margin,
                    quality=face_quality.score,
                    rejected_reason=reason,
                    status=status,
                    embedding=embedding,
                    tta_used=tta_used,
                )
            )
        self._last_timings_ms = {
            "detector": detector_ms,
            "recognizer": recognizer_ms,
        }
        return tuple(results)

    def identify_tiled(
        self,
        image_bgr: np.ndarray,
        *,
        rows: int = 2,
        columns: int = 2,
        overlap: float = 0.15,
        include_full_frame: bool = True,
        nms_iou_threshold: float = 0.35,
        containment_threshold: float = 0.80,
        extract_embeddings: bool = True,
    ) -> tuple[FaceIdentityDetection, ...]:
        """전체 프레임과 겹침 타일을 추론하고 중복 얼굴을 제거한다."""
        if image_bgr is None or image_bgr.size == 0:
            return ()
        if rows < 1 or columns < 1:
            raise ValueError("타일 행과 열은 1 이상이어야 합니다.")
        if not 0.0 <= overlap < 0.5:
            raise ValueError("타일 overlap은 0 이상 0.5 미만이어야 합니다.")
        if not 0.0 <= nms_iou_threshold <= 1.0:
            raise ValueError("NMS IoU 임계값은 0과 1 사이여야 합니다.")
        if not 0.0 <= containment_threshold <= 1.0:
            raise ValueError("박스 포함 임계값은 0과 1 사이여야 합니다.")

        height, width = image_bgr.shape[:2]
        detector_ms = 0.0
        recognizer_ms = 0.0
        if include_full_frame:
            candidates = list(
                self.identify(
                    image_bgr,
                    extract_embeddings=extract_embeddings,
                )
            )
            detector_ms += self._last_timings_ms["detector"]
            recognizer_ms += self._last_timings_ms["recognizer"]
        else:
            candidates = []
        tile_height = math.ceil(height / rows)
        tile_width = math.ceil(width / columns)
        y_padding = int(round(tile_height * overlap))
        x_padding = int(round(tile_width * overlap))

        for row in range(rows):
            for column in range(columns):
                left = max(0, column * tile_width - x_padding)
                top = max(0, row * tile_height - y_padding)
                right = min(width, (column + 1) * tile_width + x_padding)
                bottom = min(height, (row + 1) * tile_height + y_padding)
                tile = image_bgr[top:bottom, left:right]
                tile_detections = self.identify(
                    tile,
                    extract_embeddings=extract_embeddings,
                )
                detector_ms += self._last_timings_ms["detector"]
                recognizer_ms += self._last_timings_ms["recognizer"]
                for detection in tile_detections:
                    tile_left, tile_top, tile_right, tile_bottom = detection.bbox
                    candidates.append(
                        replace(
                            detection,
                            bbox=(
                                tile_left + left,
                                tile_top + top,
                                tile_right + left,
                                tile_bottom + top,
                            ),
                        )
                    )

        # 얼굴 안에서 다시 검출된 작은 박스는 IoU가 낮아도 작은 박스의
        # 대부분이 큰 박스에 포함된다. 먼저 큰 박스를 선택해 이런 중복을
        # 제거하고, 나머지 박스에는 기존 IoU NMS를 적용한다.
        ordered = sorted(
            candidates,
            key=lambda item: (
                _bbox_area(item.bbox),
                item.detection_confidence,
                item.quality,
            ),
            reverse=True,
        )
        selected: list[FaceIdentityDetection] = []
        for candidate in ordered:
            is_duplicate = any(
                _bbox_iou(candidate.bbox, existing.bbox) >= nms_iou_threshold
                or _bbox_intersection_over_smaller(
                    candidate.bbox,
                    existing.bbox,
                ) >= containment_threshold
                for existing in selected
            )
            if not is_duplicate:
                selected.append(candidate)
        self._last_timings_ms = {
            "detector": detector_ms,
            "recognizer": recognizer_ms,
        }
        return tuple(selected)


@dataclass
class _Track:
    bbox: tuple[int, int, int, int]
    last_seen: int
    embeddings: deque[tuple[np.ndarray, float]]


class MultiFaceIdentityTracker:
    """bbox와 임베딩으로 얼굴을 연결하고 품질 가중 특징을 누적한다."""

    def __init__(
        self,
        engine: FaceIdentityEngine,
        *,
        history_size: int = 12,
        minimum_observations: int = 4,
        minimum_evidence_quality: float = 0.2,
        maximum_center_distance: float = 120.0,
        minimum_iou: float = 0.05,
        stale_frames: int = 10,
    ) -> None:
        if history_size < minimum_observations or minimum_observations < 1:
            raise ValueError("history_size는 minimum_observations 이상이어야 합니다.")

        self._engine = engine
        self._history_size = history_size
        self._minimum_observations = minimum_observations
        self._minimum_evidence_quality = minimum_evidence_quality
        self._maximum_center_distance = maximum_center_distance
        self._minimum_iou = minimum_iou
        self._stale_frames = stale_frames
        self._tracks: dict[int, _Track] = {}
        self._next_track_id = 1
        self._frame_index = 0

    def _assign(self, detection: FaceIdentityDetection, used: set[int]) -> int:
        center = _bbox_center(detection.bbox)
        candidates: list[tuple[float, int]] = []
        for track_id, track in self._tracks.items():
            if track_id in used:
                continue
            distance = math.dist(center, _bbox_center(track.bbox))
            overlap = _bbox_iou(detection.bbox, track.bbox)
            if distance <= self._maximum_center_distance or overlap >= self._minimum_iou:
                candidates.append((distance - 50.0 * overlap, track_id))

        if candidates:
            return min(candidates)[1]

        track_id = self._next_track_id
        self._next_track_id += 1
        self._tracks[track_id] = _Track(
            bbox=detection.bbox,
            last_seen=self._frame_index,
            embeddings=deque(maxlen=self._history_size),
        )
        return track_id

    def update(
        self,
        detections: Sequence[FaceIdentityDetection],
    ) -> tuple[TrackedIdentity, ...]:
        self._frame_index += 1
        used: set[int] = set()
        results: list[TrackedIdentity] = []

        for detection in detections:
            track_id = self._assign(detection, used)
            used.add(track_id)
            track = self._tracks[track_id]
            track.bbox = detection.bbox
            track.last_seen = self._frame_index

            if (
                detection.embedding is not None
                and detection.quality >= self._minimum_evidence_quality
            ):
                track.embeddings.append((detection.embedding, detection.quality))

            if len(track.embeddings) < self._minimum_observations:
                status = IdentityStatus.UNCERTAIN
                student_id = None
                similarity = detection.similarity
                margin = detection.margin
            else:
                vectors = np.stack([item[0] for item in track.embeddings])
                weights = np.asarray(
                    [max(item[1], 1e-3) for item in track.embeddings],
                    dtype=np.float32,
                )
                averaged = normalize_embedding(np.average(vectors, axis=0, weights=weights))
                aggregate_quality = float(np.average(weights, weights=weights))
                status, student_id, similarity, margin, _ = self._engine.match_embedding(
                    averaged,
                    quality=aggregate_quality,
                )

            results.append(
                TrackedIdentity(
                    track_id=track_id,
                    bbox=detection.bbox,
                    status=status,
                    student_id=student_id,
                    similarity=similarity,
                    margin=margin,
                    quality=detection.quality,
                    observation_count=len(track.embeddings),
                )
            )

        self._tracks = {
            track_id: track
            for track_id, track in self._tracks.items()
            if self._frame_index - track.last_seen <= self._stale_frames
        }
        return tuple(results)


class TemporalIdentityConsensus:
    """이전 v7 호출부와의 호환성을 위한 판정 다수결 클래스."""

    def __init__(self, window_size: int = 5, consensus_count: int = 4) -> None:
        if not 1 <= consensus_count <= window_size:
            raise ValueError("consensus_count는 1 이상 window_size 이하여야 합니다.")
        self._window_size = window_size
        self._consensus_count = consensus_count
        self._history: dict[str, deque[str | None]] = {}

    def update(self, track_id: str, student_id: str | None) -> str | None:
        history = self._history.setdefault(
            track_id,
            deque(maxlen=self._window_size),
        )
        history.append(student_id)
        if len(history) < self._window_size:
            return None

        counts = Counter(value for value in history if value is not None)
        if not counts:
            return None
        candidate, count = counts.most_common(1)[0]
        return candidate if count >= self._consensus_count else None

    def discard(self, track_id: str) -> None:
        self._history.pop(track_id, None)
