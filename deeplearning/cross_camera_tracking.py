"""두 카메라의 로컬 ByteTrack을 단방향 global track으로 연결한다."""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np
from scipy.optimize import linear_sum_assignment

from .homecam_tracking import TrackIdentityStatus
from .person_reid import normalize_feature

Point = tuple[float, float]
CameraTrackKey = tuple[str, int]


@dataclass(frozen=True)
class CrossCameraCalibration:
    entry_resolution: tuple[int, int]
    classroom_resolution: tuple[int, int]
    entry_overlap_polygon: tuple[Point, ...]
    classroom_overlap_polygon: tuple[Point, ...]
    entry_correspondence_points: tuple[Point, ...]
    classroom_correspondence_points: tuple[Point, ...]
    direction: str = "entry_to_classroom"

    def __post_init__(self) -> None:
        if len(self.entry_overlap_polygon) < 3 or len(self.classroom_overlap_polygon) < 3:
            raise ValueError("각 겹침 구역 폴리곤에는 점이 3개 이상 필요합니다.")
        if len(self.entry_correspondence_points) < 4 or len(
            self.entry_correspondence_points
        ) != len(self.classroom_correspondence_points):
            raise ValueError("두 카메라에 같은 수의 대응점이 4개 이상 필요합니다.")
        for point in (
            *self.entry_overlap_polygon,
            *self.classroom_overlap_polygon,
            *self.entry_correspondence_points,
            *self.classroom_correspondence_points,
        ):
            if not all(0.0 <= value <= 1.0 for value in point):
                raise ValueError("보정 좌표는 0과 1 사이의 정규화 좌표여야 합니다.")

    @property
    def homography(self) -> np.ndarray:
        source = np.asarray(self.entry_correspondence_points, dtype=np.float32)
        target = np.asarray(self.classroom_correspondence_points, dtype=np.float32)
        matrix, _ = cv2.findHomography(source, target, method=0)
        if matrix is None:
            raise ValueError("대응점으로 homography를 계산하지 못했습니다.")
        return matrix

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> CrossCameraCalibration:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            entry_resolution=tuple(data["entry_resolution"]),
            classroom_resolution=tuple(data["classroom_resolution"]),
            entry_overlap_polygon=tuple(map(tuple, data["entry_overlap_polygon"])),
            classroom_overlap_polygon=tuple(
                map(tuple, data["classroom_overlap_polygon"])
            ),
            entry_correspondence_points=tuple(
                map(tuple, data["entry_correspondence_points"])
            ),
            classroom_correspondence_points=tuple(
                map(tuple, data["classroom_correspondence_points"])
            ),
            direction=data.get("direction", "entry_to_classroom"),
        )


def point_in_polygon(point: Point, polygon: tuple[Point, ...]) -> bool:
    contour = np.asarray(polygon, dtype=np.float32)
    return cv2.pointPolygonTest(contour, point, False) >= 0


def project_point(point: Point, homography: np.ndarray) -> Point:
    source = np.asarray([[point]], dtype=np.float32)
    projected = cv2.perspectiveTransform(source, homography)[0, 0]
    return float(projected[0]), float(projected[1])


@dataclass(frozen=True)
class TrackObservation:
    camera_id: str
    local_track_id: int
    foot_point: Point
    timestamp: float
    feature: np.ndarray | None = field(repr=False, compare=False)

    @property
    def key(self) -> CameraTrackKey:
        return self.camera_id, self.local_track_id


@dataclass(frozen=True)
class IdentityPayload:
    status: TrackIdentityStatus
    student_id: str | None


@dataclass(frozen=True)
class MatchScore:
    entry_track_id: int
    classroom_track_id: int
    appearance: float
    geometry: float
    time: float
    time_difference_seconds: float
    total: float
    accepted: bool
    reason: str


@dataclass(frozen=True)
class GlobalTrackView:
    global_track_id: str
    identity: IdentityPayload


@dataclass
class _Mapping:
    global_track_id: str
    last_seen: float


class CrossCameraTracker:
    """입구 트랙의 global ID를 교실 트랙에 보수적으로 인계한다."""

    def __init__(
        self,
        calibration: CrossCameraCalibration,
        *,
        appearance_weight: float = 0.60,
        geometry_weight: float = 0.25,
        time_weight: float = 0.15,
        minimum_score: float = 0.70,
        minimum_margin: float = 0.08,
        maximum_time_difference: float = 0.50,
        maximum_geometry_distance: float = 0.20,
        stale_seconds: float = 2.0,
    ) -> None:
        weights = appearance_weight + geometry_weight + time_weight
        if not math.isclose(weights, 1.0, abs_tol=1e-6):
            raise ValueError("교차 매칭 가중치 합은 1이어야 합니다.")
        if minimum_margin < 0 or maximum_time_difference <= 0:
            raise ValueError("매칭 시간과 margin 설정이 올바르지 않습니다.")
        self.calibration = calibration
        self.appearance_weight = appearance_weight
        self.geometry_weight = geometry_weight
        self.time_weight = time_weight
        self.minimum_score = minimum_score
        self.minimum_margin = minimum_margin
        self.maximum_time_difference = maximum_time_difference
        self.maximum_geometry_distance = maximum_geometry_distance
        self.stale_seconds = stale_seconds
        self._next_global_id = 1
        self._mappings: dict[CameraTrackKey, _Mapping] = {}
        self._identities: dict[str, IdentityPayload] = {}
        self.diagnostics: Counter[str] = Counter()
        self.score_history: list[dict[str, object]] = []

    def register_entry(
        self,
        observation: TrackObservation,
        identity: IdentityPayload,
    ) -> GlobalTrackView:
        mapping = self._mappings.get(observation.key)
        if mapping is None:
            mapping = _Mapping(f"G{self._next_global_id:04d}", observation.timestamp)
            self._next_global_id += 1
            self._mappings[observation.key] = mapping
        mapping.last_seen = observation.timestamp
        current = self._identities.get(mapping.global_track_id)
        if current is None or current.status is not TrackIdentityStatus.REGISTERED:
            self._identities[mapping.global_track_id] = identity
        elif (
            identity.status is TrackIdentityStatus.REGISTERED
            and current.student_id != identity.student_id
        ):
            self.diagnostics["identity_overwrite_blocked"] += 1
        return GlobalTrackView(mapping.global_track_id, self._identities[mapping.global_track_id])

    def lookup(self, key: CameraTrackKey, *, now: float | None = None) -> GlobalTrackView | None:
        mapping = self._mappings.get(key)
        if mapping is None:
            return None
        if now is not None:
            mapping.last_seen = now
        return GlobalTrackView(
            mapping.global_track_id,
            self._identities[mapping.global_track_id],
        )

    def match(
        self,
        entry_observations: list[TrackObservation],
        classroom_observations: list[TrackObservation],
    ) -> tuple[MatchScore, ...]:
        entries = [
            item
            for item in entry_observations
            if item.feature is not None
            and point_in_polygon(item.foot_point, self.calibration.entry_overlap_polygon)
            and item.key in self._mappings
        ]
        classrooms = [
            item
            for item in classroom_observations
            if item.feature is not None
            and point_in_polygon(
                item.foot_point, self.calibration.classroom_overlap_polygon
            )
            and item.key not in self._mappings
        ]
        if not entries or not classrooms:
            return ()
        self.diagnostics["handoff_attempts"] += len(entries)

        totals = np.full((len(entries), len(classrooms)), -1.0, dtype=np.float32)
        components: dict[tuple[int, int], tuple[float, float, float]] = {}
        homography = self.calibration.homography
        for row, entry in enumerate(entries):
            projected = project_point(entry.foot_point, homography)
            for column, classroom in enumerate(classrooms):
                time_difference = abs(entry.timestamp - classroom.timestamp)
                if time_difference > self.maximum_time_difference:
                    continue
                appearance = max(
                    0.0,
                    min(
                        1.0,
                        float(
                            np.dot(
                                normalize_feature(entry.feature),
                                normalize_feature(classroom.feature),
                            )
                        ),
                    ),
                )
                distance = math.dist(projected, classroom.foot_point)
                geometry = max(0.0, 1.0 - distance / self.maximum_geometry_distance)
                time_score = max(
                    0.0, 1.0 - time_difference / self.maximum_time_difference
                )
                total = (
                    self.appearance_weight * appearance
                    + self.geometry_weight * geometry
                    + self.time_weight * time_score
                )
                totals[row, column] = total
                components[(row, column)] = (appearance, geometry, time_score)

        valid_cost = np.where(totals >= 0.0, 1.0 - totals, 1e6)
        rows, columns = linear_sum_assignment(valid_cost)
        results: list[MatchScore] = []
        for row, column in zip(rows.tolist(), columns.tolist()):
            total = float(totals[row, column])
            if total < 0:
                continue
            alternatives = [
                float(value)
                for index, value in enumerate(totals[row])
                if index != column and value >= 0
            ] + [
                float(value)
                for index, value in enumerate(totals[:, column])
                if index != row and value >= 0
            ]
            margin = total - max(alternatives, default=0.0)
            accepted = total >= self.minimum_score and margin >= self.minimum_margin
            reason = "accepted" if accepted else (
                "low_score" if total < self.minimum_score else "ambiguous"
            )
            appearance, geometry, time_score = components[(row, column)]
            time_difference = abs(
                entries[row].timestamp - classrooms[column].timestamp
            )
            score = MatchScore(
                entries[row].local_track_id,
                classrooms[column].local_track_id,
                appearance,
                geometry,
                time_score,
                time_difference,
                total,
                accepted,
                reason,
            )
            results.append(score)
            self.score_history.append(asdict(score))
            if accepted:
                source = self._mappings[entries[row].key]
                self._mappings[classrooms[column].key] = _Mapping(
                    source.global_track_id,
                    classrooms[column].timestamp,
                )
                self.diagnostics["handoff_successes"] += 1
            else:
                self.diagnostics["handoff_deferred"] += 1
        return tuple(results)

    def expire(self, *, now: float) -> None:
        expired = [
            key
            for key, mapping in self._mappings.items()
            if now - mapping.last_seen > self.stale_seconds
        ]
        for key in expired:
            del self._mappings[key]
        self.diagnostics["expired_local_mappings"] += len(expired)

    def snapshot(self) -> dict[str, object]:
        return {
            "counts": dict(sorted(self.diagnostics.items())),
            "active_local_mappings": len(self._mappings),
            "global_tracks_created": self._next_global_id - 1,
            "candidate_scores": self.score_history,
        }
