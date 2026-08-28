"""사람 탐지에 카메라별 ByteTrack ID를 붙이는 결과 핸들러.

모델 추론은 한 번만 하고, 그 결과 중 사람 bbox만 ByteTrack의 두 단계 연관 규칙으로
이어 본다. 1단계는 신뢰도 높은 탐지를, 2단계는 낮은 탐지를 유실 후보와 대조한다.
낮은 신뢰도 bbox까지 활용해 가림 직후의 ID 단절을 줄이는 것이 ByteTrack의 핵심이다.

카메라마다 상태와 ID 공간을 분리한다. 여러 RTSP가 한 프로세스에 섞여 들어오므로
하나의 tracker를 공유하면 서로 다른 카메라의 사람이 같은 track으로 이어질 수 있다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace

import numpy as np
from shared.types import CapturedFrame

from .consumer import ResultHandler
from .metrics import (
    PERSON_TRACK_LIFETIME_FRAMES,
    PERSON_TRACKS_ACTIVE,
    PERSON_TRACKS_CREATED_TOTAL,
    PERSON_TRACKS_EXPIRED_TOTAL,
)
from .types import BBox, Detection, InferenceResult


def _bbox_array(bbox: BBox) -> np.ndarray:
    return np.asarray(bbox, dtype=np.float64)


def _iou(left: np.ndarray, right: np.ndarray) -> float:
    intersection_left = max(left[0], right[0])
    intersection_top = max(left[1], right[1])
    intersection_right = min(left[2], right[2])
    intersection_bottom = min(left[3], right[3])
    intersection = max(0.0, intersection_right - intersection_left) * max(
        0.0, intersection_bottom - intersection_top
    )
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return 0.0 if union <= 0.0 else intersection / union


def _area(bbox: np.ndarray) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _ios(left: np.ndarray, right: np.ndarray) -> float:
    """교집합을 더 작은 bbox 면적으로 나눈 포함 비율을 반환한다."""

    intersection_left = max(left[0], right[0])
    intersection_top = max(left[1], right[1])
    intersection_right = min(left[2], right[2])
    intersection_bottom = min(left[3], right[3])
    intersection = max(0.0, intersection_right - intersection_left) * max(
        0.0, intersection_bottom - intersection_top
    )
    smaller_area = min(_area(left), _area(right))
    return 0.0 if smaller_area <= 0.0 else intersection / smaller_area


def _suppress_duplicate_people(
    people: Sequence[tuple[int, Detection]],
    *,
    iou_threshold: float,
    ios_threshold: float,
) -> tuple[tuple[int, Detection], ...]:
    """사람 클래스에만 포함 관계 우선 NMS를 적용한다.

    IoS가 임계값 이상이면 더 큰 bbox를 남긴다. 그 외 중복은 confidence가 높은
    탐지를 우선하고, 완전히 같으면 모델 입력 순서를 유지한다.
    """

    boxes = [_bbox_array(detection.bbox) for _, detection in people]
    suppressed: set[int] = set()

    # 포함 관계에서는 작은 bbox의 confidence가 더 높아도 큰 bbox를 남긴다.
    for left_index, left_box in enumerate(boxes):
        for right_index in range(left_index + 1, len(boxes)):
            right_box = boxes[right_index]
            if _ios(left_box, right_box) < ios_threshold:
                continue
            left_area = _area(left_box)
            right_area = _area(right_box)
            if left_area < right_area:
                suppressed.add(left_index)
            elif right_area < left_area:
                suppressed.add(right_index)

    candidates = [
        index for index in range(len(people)) if index not in suppressed
    ]
    candidates.sort(key=lambda index: (-people[index][1].confidence, index))
    kept: list[int] = []
    for candidate in candidates:
        if any(
            _iou(boxes[candidate], boxes[existing]) >= iou_threshold
            or _ios(boxes[candidate], boxes[existing]) >= ios_threshold
            for existing in kept
        ):
            continue
        kept.append(candidate)
    kept.sort()
    return tuple(people[index] for index in kept)


def _minimum_cost_assignment(costs: np.ndarray) -> tuple[tuple[int, int], ...]:
    """직사각 비용 행렬의 최소 일대일 배정을 Hungarian 알고리즘으로 구한다."""
    if costs.ndim != 2:
        raise ValueError("배정 비용은 2차원이어야 합니다.")
    row_count, column_count = costs.shape
    if row_count == 0 or column_count == 0:
        return ()

    transposed = row_count > column_count
    matrix = costs.T if transposed else costs
    rows, columns = matrix.shape
    # 1-based cp-algorithms 구현. rows <= columns인 경우만 계산한다.
    row_potential = np.zeros(rows + 1, dtype=np.float64)
    column_potential = np.zeros(columns + 1, dtype=np.float64)
    matched_row = np.zeros(columns + 1, dtype=np.int64)
    previous_column = np.zeros(columns + 1, dtype=np.int64)

    for row in range(1, rows + 1):
        matched_row[0] = row
        column = 0
        minimum = np.full(columns + 1, np.inf, dtype=np.float64)
        used = np.zeros(columns + 1, dtype=bool)
        while True:
            used[column] = True
            current_row = int(matched_row[column])
            delta = np.inf
            next_column = 0
            for candidate in range(1, columns + 1):
                if used[candidate]:
                    continue
                reduced = (
                    matrix[current_row - 1, candidate - 1]
                    - row_potential[current_row]
                    - column_potential[candidate]
                )
                if reduced < minimum[candidate]:
                    minimum[candidate] = reduced
                    previous_column[candidate] = column
                if minimum[candidate] < delta:
                    delta = minimum[candidate]
                    next_column = candidate
            for candidate in range(columns + 1):
                if used[candidate]:
                    row_potential[matched_row[candidate]] += delta
                    column_potential[candidate] -= delta
                else:
                    minimum[candidate] -= delta
            column = next_column
            if matched_row[column] == 0:
                break
        while True:
            previous = int(previous_column[column])
            matched_row[column] = matched_row[previous]
            column = previous
            if column == 0:
                break

    pairs = [
        (int(matched_row[column]) - 1, column - 1)
        for column in range(1, columns + 1)
        if matched_row[column] != 0
    ]
    if transposed:
        return tuple((column, row) for row, column in pairs)
    return tuple(pairs)


@dataclass
class _Track:
    track_id: int
    bbox: np.ndarray
    last_observed_at: float
    velocity: np.ndarray = field(default_factory=lambda: np.zeros(4, dtype=np.float64))
    hits: int = 1
    missed_frames: int = 0
    age_frames: int = 1

    def predicted_bbox(self, observed_at: float) -> np.ndarray:
        elapsed_seconds = max(0.0, observed_at - self.last_observed_at)
        return self.bbox + self.velocity * elapsed_seconds

    def update(self, bbox: np.ndarray, observed_at: float) -> None:
        elapsed_seconds = observed_at - self.last_observed_at
        if elapsed_seconds > 1e-6:
            observed_velocity = (bbox - self.bbox) / elapsed_seconds
            if self.hits == 1:
                self.velocity = observed_velocity
            else:
                # 짧은 bbox 흔들림보다 최근 이동 방향을 더 오래 유지한다.
                self.velocity = self.velocity * 0.7 + observed_velocity * 0.3
        self.bbox = bbox
        self.last_observed_at = max(self.last_observed_at, observed_at)
        self.hits += 1
        self.missed_frames = 0


@dataclass(frozen=True)
class ByteTrackConfig:
    high_confidence_threshold: float = 0.5
    low_confidence_threshold: float = 0.1
    new_track_threshold: float = 0.6
    first_match_iou_threshold: float = 0.3
    second_match_iou_threshold: float = 0.2
    track_buffer_frames: int = 30
    person_detection_postprocess_enabled: bool = False
    duplicate_iou_threshold: float = 0.5
    duplicate_ios_threshold: float = 0.85

    def __post_init__(self) -> None:
        thresholds = (
            self.high_confidence_threshold,
            self.low_confidence_threshold,
            self.new_track_threshold,
            self.first_match_iou_threshold,
            self.second_match_iou_threshold,
            self.duplicate_iou_threshold,
            self.duplicate_ios_threshold,
        )
        if any(not 0.0 <= value <= 1.0 for value in thresholds):
            raise ValueError("ByteTrack 임계값은 0과 1 사이여야 합니다.")
        if self.low_confidence_threshold > self.high_confidence_threshold:
            raise ValueError("낮은 신뢰도 임계값은 높은 임계값보다 클 수 없습니다.")
        if self.new_track_threshold < self.high_confidence_threshold:
            raise ValueError("새 track 임계값은 높은 신뢰도 임계값 이상이어야 합니다.")
        if self.track_buffer_frames < 1:
            raise ValueError("track buffer는 1프레임 이상이어야 합니다.")


class CameraByteTracker:
    """한 카메라의 사람 detection을 ByteTrack 방식으로 이어 본다."""

    def __init__(self, config: ByteTrackConfig) -> None:
        self._config = config
        self._tracks: dict[int, _Track] = {}
        self._next_track_id = 1
        self.created_last_update = 0
        self.expired_last_update = 0
        self.expired_lifetimes_last_update: tuple[int, ...] = ()
        self.expired_track_ids_last_update: tuple[str, ...] = ()
        self._update_index = 0

    @property
    def active_track_count(self) -> int:
        return len(self._tracks)

    def _match(
        self,
        track_ids: Sequence[int],
        detections: Sequence[tuple[int, Detection]],
        *,
        minimum_iou: float,
        observed_at: float,
    ) -> tuple[tuple[tuple[int, int], ...], tuple[int, ...], tuple[int, ...]]:
        if not track_ids or not detections:
            return (), tuple(track_ids), tuple(range(len(detections)))
        ious = np.asarray(
            [
                [
                    _iou(
                        self._tracks[track_id].predicted_bbox(observed_at),
                        _bbox_array(detection.bbox),
                    )
                    for _, detection in detections
                ]
                for track_id in track_ids
            ],
            dtype=np.float64,
        )
        assigned = _minimum_cost_assignment(1.0 - ious)
        matches = tuple(
            (row, column)
            for row, column in assigned
            if ious[row, column] >= minimum_iou
        )
        matched_rows = {row for row, _ in matches}
        matched_columns = {column for _, column in matches}
        return (
            matches,
            tuple(
                track_ids[row]
                for row in range(len(track_ids))
                if row not in matched_rows
            ),
            tuple(
                column
                for column in range(len(detections))
                if column not in matched_columns
            ),
        )

    def update(
        self, result: InferenceResult, *, observed_at: float | None = None
    ) -> InferenceResult:
        self._update_index += 1
        current_observed_at = (
            float(self._update_index) if observed_at is None else observed_at
        )
        self.created_last_update = 0
        self.expired_last_update = 0
        self.expired_lifetimes_last_update = ()
        self.expired_track_ids_last_update = ()
        for track in self._tracks.values():
            track.age_frames += 1
        people: Sequence[tuple[int, Detection]] = [
            (index, detection)
            for index, detection in enumerate(result.detections)
            if detection.class_name.casefold() == "person"
        ]
        if self._config.person_detection_postprocess_enabled:
            people = _suppress_duplicate_people(
                people,
                iou_threshold=self._config.duplicate_iou_threshold,
                ios_threshold=self._config.duplicate_ios_threshold,
            )
        high = [
            item
            for item in people
            if item[1].confidence >= self._config.high_confidence_threshold
        ]
        low = [
            item
            for item in people
            if self._config.low_confidence_threshold
            <= item[1].confidence
            < self._config.high_confidence_threshold
        ]
        track_ids = tuple(sorted(self._tracks))
        assignments: dict[int, int] = {}

        first_matches, unmatched_track_ids, unmatched_high_columns = self._match(
            track_ids,
            high,
            minimum_iou=self._config.first_match_iou_threshold,
            observed_at=current_observed_at,
        )
        for track_row, detection_column in first_matches:
            track_id = track_ids[track_row]
            detection_index, detection = high[detection_column]
            self._tracks[track_id].update(
                _bbox_array(detection.bbox), current_observed_at
            )
            assignments[detection_index] = track_id

        second_matches, still_unmatched_track_ids, _ = self._match(
            unmatched_track_ids,
            low,
            minimum_iou=self._config.second_match_iou_threshold,
            observed_at=current_observed_at,
        )
        for track_row, detection_column in second_matches:
            track_id = unmatched_track_ids[track_row]
            detection_index, detection = low[detection_column]
            self._tracks[track_id].update(
                _bbox_array(detection.bbox), current_observed_at
            )
            assignments[detection_index] = track_id

        for track_id in still_unmatched_track_ids:
            self._tracks[track_id].missed_frames += 1

        for column in unmatched_high_columns:
            detection_index, detection = high[column]
            if detection.confidence < self._config.new_track_threshold:
                continue
            track_id = self._next_track_id
            self._next_track_id += 1
            self._tracks[track_id] = _Track(
                track_id,
                _bbox_array(detection.bbox),
                last_observed_at=current_observed_at,
            )
            assignments[detection_index] = track_id
            self.created_last_update += 1

        expired = [
            track_id
            for track_id, track in self._tracks.items()
            if track.missed_frames > self._config.track_buffer_frames
        ]
        self.expired_lifetimes_last_update = tuple(
            self._tracks[track_id].age_frames for track_id in expired
        )
        self.expired_track_ids_last_update = tuple(
            f"person-{track_id}" for track_id in expired
        )
        for track_id in expired:
            del self._tracks[track_id]
        self.expired_last_update = len(expired)

        retained_person_indices = {index for index, _ in people}
        enriched: list[Detection] = []
        for index, detection in enumerate(result.detections):
            is_person = detection.class_name.casefold() == "person"
            if self._config.person_detection_postprocess_enabled and is_person:
                if index not in retained_person_indices:
                    continue
                if (
                    detection.confidence
                    < self._config.high_confidence_threshold
                    and index not in assignments
                ):
                    continue
            enriched.append(
                replace(detection, track_id=f"person-{assignments[index]}")
                if index in assignments
                else detection
            )
        return InferenceResult(result.frame_shape, tuple(enriched))


class ByteTrackResultHandler:
    """카메라별 tracker를 유지한 뒤 결과를 다음 핸들러로 전달한다."""

    def __init__(
        self,
        config: ByteTrackConfig,
        *,
        inner: ResultHandler,
        camera_ids: frozenset[str] | None = None,
        tracker_factory: Callable[
            [ByteTrackConfig], CameraByteTracker
        ] = CameraByteTracker,
        expired_track_handler: Callable[[str, tuple[str, ...]], None] | None = None,
    ) -> None:
        self._config = config
        self._inner = inner
        self._camera_ids = camera_ids
        self._tracker_factory = tracker_factory
        self._expired_track_handler = expired_track_handler
        self._trackers: dict[str, CameraByteTracker] = {}

    def __call__(self, captured: CapturedFrame, result: InferenceResult) -> None:
        if self._camera_ids is not None and captured.camera_id not in self._camera_ids:
            self._inner(captured, result)
            return
        tracker = self._trackers.get(captured.camera_id)
        if tracker is None:
            tracker = self._tracker_factory(self._config)
            self._trackers[captured.camera_id] = tracker
        tracked = tracker.update(result, observed_at=captured.captured_at.timestamp())
        if tracker.created_last_update:
            PERSON_TRACKS_CREATED_TOTAL.labels(camera_id=captured.camera_id).inc(
                tracker.created_last_update
            )
        if tracker.expired_last_update:
            PERSON_TRACKS_EXPIRED_TOTAL.labels(camera_id=captured.camera_id).inc(
                tracker.expired_last_update
            )
            for lifetime in tracker.expired_lifetimes_last_update:
                PERSON_TRACK_LIFETIME_FRAMES.labels(
                    camera_id=captured.camera_id
                ).observe(lifetime)
            if self._expired_track_handler is not None:
                self._expired_track_handler(
                    captured.camera_id, tracker.expired_track_ids_last_update
                )
        PERSON_TRACKS_ACTIVE.labels(camera_id=captured.camera_id).set(
            tracker.active_track_count
        )
        self._inner(captured, tracked)


__all__ = [
    "ByteTrackConfig",
    "ByteTrackResultHandler",
    "CameraByteTracker",
]
