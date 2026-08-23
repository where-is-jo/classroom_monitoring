"""홈캠 사람 트랙에 얼굴 식별 증거를 안전하게 연결한다."""

from __future__ import annotations

import time
from collections import Counter, defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from .face_identity import IdentityStatus

BBox = tuple[int, int, int, int]


class FaceEvidence(Protocol):
    bbox: BBox
    detection_confidence: float
    student_id: str | None
    similarity: float
    margin: float
    quality: float
    status: IdentityStatus


@dataclass(frozen=True)
class PersonTrack:
    track_id: int
    bbox: BBox
    confidence: float


class TrackIdentityStatus(str, Enum):
    REGISTERED = "registered"
    UNKNOWN = "unknown"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class FacePersonAssociation:
    person_track_id: int | None
    face_index: int
    reason: str
    candidate_person_track_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class TrackIdentity:
    track_id: int
    status: TrackIdentityStatus
    student_id: str | None
    similarity: float
    margin: float
    observation_count: int
    bbox: BBox
    last_seen_frame: int


def _area(bbox: BBox) -> int:
    return max(0, bbox[2] - bbox[0]) * max(0, bbox[3] - bbox[1])


def _face_coverage(face: BBox, person: BBox) -> float:
    left = max(face[0], person[0])
    top = max(face[1], person[1])
    right = min(face[2], person[2])
    bottom = min(face[3], person[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    return intersection / max(1, _area(face))


def associate_faces_to_people(
    people: Sequence[PersonTrack],
    faces: Sequence[FaceEvidence],
    *,
    minimum_face_coverage: float = 0.8,
) -> tuple[FacePersonAssociation, ...]:
    """얼굴 중심과 얼굴 포함률로 일대일 연결하고 모호한 경우를 거부한다."""
    if not 0.0 <= minimum_face_coverage <= 1.0:
        raise ValueError("minimum_face_coverage는 0과 1 사이여야 합니다.")

    candidate_ids: list[list[int]] = []
    for face in faces:
        center_x = (face.bbox[0] + face.bbox[2]) / 2.0
        center_y = (face.bbox[1] + face.bbox[3]) / 2.0
        candidates = [
            person.track_id
            for person in people
            if person.bbox[0] <= center_x <= person.bbox[2]
            and person.bbox[1] <= center_y <= person.bbox[3]
            and _face_coverage(face.bbox, person.bbox) >= minimum_face_coverage
        ]
        candidate_ids.append(candidates)

    unique_owner_counts = Counter(
        candidates[0] for candidates in candidate_ids if len(candidates) == 1
    )
    results: list[FacePersonAssociation] = []
    for face_index, candidates in enumerate(candidate_ids):
        if not candidates:
            results.append(FacePersonAssociation(None, face_index, "no_person"))
        elif len(candidates) > 1:
            results.append(
                FacePersonAssociation(
                    None,
                    face_index,
                    "multiple_people",
                    tuple(candidates),
                )
            )
        elif unique_owner_counts[candidates[0]] > 1:
            results.append(
                FacePersonAssociation(
                    None,
                    face_index,
                    "multiple_faces",
                    (candidates[0],),
                )
            )
        else:
            results.append(
                FacePersonAssociation(
                    candidates[0],
                    face_index,
                    "matched",
                    (candidates[0],),
                )
            )
    return tuple(results)


@dataclass
class _IdentityMemory:
    bbox: BBox
    last_seen_frame: int
    first_observed_at: float | None = None
    confirmed_at: float | None = None
    evidence: deque[tuple[IdentityStatus, str | None, float, float]] = field(
        default_factory=deque
    )
    locked_student_id: str | None = None
    locked_status: TrackIdentityStatus | None = None


class PersonTrackIdentityStore:
    """ByteTrack 수명 동안 얼굴 증거를 누적하고 확정 신원을 유지한다."""

    def __init__(
        self,
        *,
        history_size: int = 12,
        minimum_observations: int = 4,
        stale_frames: int = 30,
    ) -> None:
        if minimum_observations < 1 or history_size < minimum_observations:
            raise ValueError("history_size는 minimum_observations 이상이어야 합니다.")
        if stale_frames < 1:
            raise ValueError("stale_frames는 1 이상이어야 합니다.")
        self._history_size = history_size
        self._minimum_observations = minimum_observations
        self._stale_frames = stale_frames
        self._frame_index = 0
        self._memories: dict[int, _IdentityMemory] = {}
        self.identity_switch_count = 0
        self.confirmation_durations: list[float] = []

    @property
    def active_track_ids(self) -> frozenset[int]:
        return frozenset(self._memories)

    def update(
        self,
        people: Sequence[PersonTrack],
        faces: Sequence[FaceEvidence],
        associations: Sequence[FacePersonAssociation],
        *,
        now: float | None = None,
    ) -> tuple[TrackIdentity, ...]:
        self._frame_index += 1
        observed_at = time.monotonic() if now is None else now
        face_by_track = {
            association.person_track_id: faces[association.face_index]
            for association in associations
            if association.person_track_id is not None
            and association.reason == "matched"
        }
        ambiguous_track_ids = {
            track_id
            for association in associations
            if association.reason in {"multiple_people", "multiple_faces"}
            for track_id in association.candidate_person_track_ids
        }

        results: list[TrackIdentity] = []
        for person in people:
            memory = self._memories.get(person.track_id)
            if memory is None:
                memory = _IdentityMemory(
                    bbox=person.bbox,
                    last_seen_frame=self._frame_index,
                    evidence=deque(maxlen=self._history_size),
                )
                self._memories[person.track_id] = memory
            memory.bbox = person.bbox
            memory.last_seen_frame = self._frame_index

            face = face_by_track.get(person.track_id)
            if face is not None:
                if memory.first_observed_at is None:
                    memory.first_observed_at = observed_at
                memory.evidence.append(
                    (face.status, face.student_id, face.similarity, face.margin)
                )
                self._try_confirm(memory, observed_at)

            results.append(
                self._snapshot(
                    person.track_id,
                    memory,
                    ambiguous=person.track_id in ambiguous_track_ids,
                )
            )

        self._memories = {
            track_id: memory
            for track_id, memory in self._memories.items()
            if self._frame_index - memory.last_seen_frame <= self._stale_frames
        }
        return tuple(results)

    def _try_confirm(self, memory: _IdentityMemory, observed_at: float) -> None:
        if len(memory.evidence) < self._minimum_observations:
            return
        registered_ids = [
            student_id
            for status, student_id, _, _ in memory.evidence
            if status is IdentityStatus.REGISTERED and student_id is not None
        ]
        unknown_count = sum(
            status is IdentityStatus.UNKNOWN for status, _, _, _ in memory.evidence
        )
        candidate = Counter(registered_ids).most_common(1)
        new_student_id: str | None = None
        new_status: TrackIdentityStatus | None = None
        if candidate and candidate[0][1] >= self._minimum_observations:
            new_student_id = candidate[0][0]
            new_status = TrackIdentityStatus.REGISTERED
        elif unknown_count >= self._minimum_observations:
            new_status = TrackIdentityStatus.UNKNOWN
        if new_status is None:
            return

        if (
            memory.locked_status is TrackIdentityStatus.REGISTERED
            and new_status is TrackIdentityStatus.REGISTERED
            and memory.locked_student_id != new_student_id
        ):
            self.identity_switch_count += 1
            return
        if memory.locked_status is None:
            memory.confirmed_at = observed_at
            if memory.first_observed_at is not None:
                self.confirmation_durations.append(
                    observed_at - memory.first_observed_at
                )
        memory.locked_status = new_status
        memory.locked_student_id = new_student_id

    def _snapshot(
        self,
        track_id: int,
        memory: _IdentityMemory,
        *,
        ambiguous: bool = False,
    ) -> TrackIdentity:
        if memory.locked_status is not None:
            status = memory.locked_status
            student_id = memory.locked_student_id
        else:
            status = TrackIdentityStatus.UNCERTAIN
            student_id = None
        similarities = [item[2] for item in memory.evidence]
        margins = [item[3] for item in memory.evidence]
        return TrackIdentity(
            track_id=track_id,
            status=status,
            student_id=student_id,
            similarity=sum(similarities) / len(similarities) if similarities else 0.0,
            margin=sum(margins) / len(margins) if margins else 0.0,
            observation_count=len(memory.evidence),
            bbox=memory.bbox,
            last_seen_frame=memory.last_seen_frame,
        )


@dataclass
class HomecamDiagnostics:
    frames: int = 0
    person_detections: int = 0
    seen_track_ids: set[int] = field(default_factory=set)
    face_detections: int = 0
    matched_faces: int = 0
    ambiguous_faces: int = 0
    recognition_statuses: Counter[str] = field(default_factory=Counter)
    similarities_by_student: dict[str, list[float]] = field(
        default_factory=lambda: defaultdict(list)
    )
    frame_durations: list[float] = field(default_factory=list)
    capture_latencies: list[float] = field(default_factory=list)

    def record(
        self,
        *,
        people: Sequence[PersonTrack],
        faces: Sequence[FaceEvidence],
        associations: Sequence[FacePersonAssociation],
        frame_duration: float,
        capture_latency: float | None,
    ) -> None:
        self.frames += 1
        self.person_detections += len(people)
        self.seen_track_ids.update(person.track_id for person in people)
        self.face_detections += len(faces)
        self.matched_faces += sum(item.reason == "matched" for item in associations)
        self.ambiguous_faces += sum(
            item.reason in {"multiple_people", "multiple_faces"}
            for item in associations
        )
        self.frame_durations.append(frame_duration)
        if capture_latency is not None:
            self.capture_latencies.append(capture_latency)
        for face in faces:
            self.recognition_statuses[face.status.value] += 1
            if face.student_id:
                self.similarities_by_student[face.student_id].append(face.similarity)

    def snapshot(self, identity_store: PersonTrackIdentityStore) -> dict[str, object]:
        average_duration = (
            sum(self.frame_durations) / len(self.frame_durations)
            if self.frame_durations
            else None
        )
        return {
            "frames": self.frames,
            "person_detection_total": self.person_detections,
            "unique_person_track_total": len(self.seen_track_ids),
            "face_detection_total": self.face_detections,
            "face_detection_per_person_rate": (
                self.face_detections / self.person_detections
                if self.person_detections
                else None
            ),
            "face_to_person_match_rate": (
                self.matched_faces / self.face_detections
                if self.face_detections
                else None
            ),
            "ambiguous_face_total": self.ambiguous_faces,
            "recognition_status_counts": dict(self.recognition_statuses),
            "similarities_by_student": {
                key: values for key, values in sorted(self.similarities_by_student.items())
            },
            "identity_switch_count": identity_store.identity_switch_count,
            "confirmation_durations_seconds": identity_store.confirmation_durations,
            "average_fps": 1.0 / average_duration if average_duration else None,
            "average_capture_latency_ms": (
                1000.0 * sum(self.capture_latencies) / len(self.capture_latencies)
                if self.capture_latencies
                else None
            ),
            "ground_truth_metrics": {
                "incorrect_name_count": None,
                "unregistered_as_registered_count": None,
                "reason": "정답 라벨이 없는 실시간 영상에서는 자동 산출할 수 없음",
            },
        }
