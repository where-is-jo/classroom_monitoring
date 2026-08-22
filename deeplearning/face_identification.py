"""등록 얼굴 갤러리와 실시간 얼굴 식별 엔진을 연결한다.

얼굴 벡터는 FastAPI 응답이나 worker payload로 내보내지 않는다. 이 모듈이 MongoDB의
대표 벡터를 읽어 메모리 갤러리를 만들고, 외부에는 학생 ID·유사도·bbox만 돌려준다.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import numpy as np

try:
    from .face_identity import (
        FaceGallery,
        FaceIdentityEngine,
        GalleryEntry,
        IdentityStatus,
        IdentityThresholds,
        MultiFaceIdentityTracker,
        TrackedIdentity,
        normalize_embedding,
    )
except ImportError:  # app.py를 `uvicorn app:app`으로 실행하는 컨테이너 경로
    from face_identity import (  # type: ignore[no-redef]
        FaceGallery,
        FaceIdentityEngine,
        GalleryEntry,
        IdentityStatus,
        IdentityThresholds,
        MultiFaceIdentityTracker,
        TrackedIdentity,
        normalize_embedding,
    )

BBox = tuple[int, int, int, int]
GalleryRevision = tuple[tuple[str, str], ...]


class FaceGalleryUnavailable(RuntimeError):
    """등록 갤러리를 안전하게 만들 수 없을 때 발생한다."""


@dataclass(frozen=True)
class FaceGallerySnapshot:
    entries: tuple[GalleryEntry, ...]
    revision: GalleryRevision


class FaceGalleryLoader(Protocol):
    def load(self) -> FaceGallerySnapshot: ...


@dataclass(frozen=True)
class FaceModelMetadata:
    model_name: str
    model_version: str
    preprocessing_version: str


class MongoFaceGalleryLoader:
    """FastAPI가 저장한 학생 대표 embedding을 읽기 전용 갤러리로 옮긴다."""

    def __init__(
        self,
        *,
        database_url: str,
        database_name: str,
        collection_name: str,
        expected_metadata: FaceModelMetadata,
        timeout_seconds: float = 5.0,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        if not database_url.strip() or not database_name.strip():
            raise ValueError(
                "얼굴 갤러리 MongoDB 주소와 데이터베이스 이름이 필요합니다."
            )
        if timeout_seconds <= 0:
            raise ValueError("얼굴 갤러리 조회 timeout은 0보다 커야 합니다.")
        self._database_url = database_url
        self._database_name = database_name
        self._collection_name = collection_name
        self._expected_metadata = expected_metadata
        self._timeout_milliseconds = round(timeout_seconds * 1000)
        self._client_factory = client_factory

    def load(self) -> FaceGallerySnapshot:
        if self._client_factory is None:
            from pymongo import MongoClient
            from pymongo.errors import PyMongoError

            client_factory: Callable[..., Any] = MongoClient
            repository_errors: tuple[type[BaseException], ...] = (PyMongoError,)
        else:
            client_factory = self._client_factory
            repository_errors = (OSError, RuntimeError)

        client: Any | None = None
        try:
            client = client_factory(
                self._database_url,
                serverSelectionTimeoutMS=self._timeout_milliseconds,
                connectTimeoutMS=self._timeout_milliseconds,
                # FastAPI 저장소와 마찬가지로 UTC aware datetime을 계약으로 유지한다.
                tz_aware=True,
            )
            client.admin.command("ping")
            projection = {
                "_id": 0,
                "student_id": 1,
                "vector": 1,
                "dimension": 1,
                "normalized": 1,
                "model_name": 1,
                "model_version": 1,
                "preprocessing_version": 1,
                "updated_at": 1,
            }
            documents = list(
                client[self._database_name][self._collection_name].find({}, projection)
            )
        except repository_errors as error:
            raise FaceGalleryUnavailable(
                "얼굴 갤러리 저장소를 조회하지 못했습니다."
            ) from error
        finally:
            if client is not None:
                client.close()

        entries: list[GalleryEntry] = []
        revision: list[tuple[str, str]] = []
        seen_student_ids: set[str] = set()
        for document in sorted(
            documents, key=lambda value: str(value.get("student_id", ""))
        ):
            student_id = document.get("student_id")
            metadata = FaceModelMetadata(
                model_name=str(document.get("model_name", "")),
                model_version=str(document.get("model_version", "")),
                preprocessing_version=str(document.get("preprocessing_version", "")),
            )
            vector = document.get("vector")
            updated_at = document.get("updated_at")
            if (
                not isinstance(student_id, str)
                or not student_id
                or student_id in seen_student_ids
                or not isinstance(vector, list)
                or document.get("dimension") != 512
                or document.get("normalized") is not True
                or metadata != self._expected_metadata
                or not isinstance(updated_at, datetime)
                or updated_at.tzinfo is None
            ):
                raise FaceGalleryUnavailable(
                    "현재 얼굴 인식 모델과 호환되지 않는 갤러리 항목이 있습니다."
                )
            try:
                raw_vector = np.asarray(vector, dtype=np.float32)
                if not np.isclose(
                    np.linalg.norm(raw_vector), 1.0, rtol=1e-3, atol=1e-3
                ):
                    raise ValueError
                normalized = normalize_embedding(raw_vector)
            except ValueError:
                raise FaceGalleryUnavailable(
                    "현재 얼굴 인식 모델과 호환되지 않는 갤러리 항목이 있습니다."
                ) from None
            seen_student_ids.add(student_id)
            entries.append(GalleryEntry(student_id, normalized))
            revision.append((student_id, updated_at.isoformat()))

        if not entries:
            raise FaceGalleryUnavailable("등록된 학생 얼굴 갤러리가 비어 있습니다.")
        return FaceGallerySnapshot(tuple(entries), tuple(revision))


@dataclass(frozen=True)
class FaceIdentificationConfig:
    similarity_threshold: float
    margin_threshold: float
    gallery_refresh_seconds: float = 30.0
    detection_threshold: float = 0.4
    identity_min_detection_confidence: float = 0.6
    minimum_face_size: int = 40
    preferred_face_size: int = 112
    minimum_blur_score: float = 20.0
    preferred_blur_score: float = 100.0
    uncertain_quality_threshold: float = 0.45
    use_flip_tta: bool = True
    tta_similarity_band: float = 0.08
    tta_margin_band: float = 0.06
    tracker_history_size: int = 12
    tracker_minimum_observations: int = 4
    tracker_stale_frames: int = 30
    minimum_face_coverage: float = 0.8

    def __post_init__(self) -> None:
        if not 0.0 <= self.similarity_threshold <= 1.0:
            raise ValueError("실시간 식별 유사도 임계값은 0과 1 사이여야 합니다.")
        if not 0.0 <= self.margin_threshold <= 2.0:
            raise ValueError("실시간 식별 margin 임계값은 0과 2 사이여야 합니다.")
        if self.gallery_refresh_seconds <= 0:
            raise ValueError("gallery refresh 주기는 0보다 커야 합니다.")
        if not 0.0 <= self.minimum_face_coverage <= 1.0:
            raise ValueError("얼굴 포함률 임계값은 0과 1 사이여야 합니다.")


@dataclass(frozen=True)
class PersonIdentity:
    person_index: int
    face_bbox: BBox
    track_id: int
    student_id: str | None
    similarity: float | None


def _area(bbox: BBox) -> int:
    return max(0, bbox[2] - bbox[0]) * max(0, bbox[3] - bbox[1])


def _face_coverage(face: BBox, person: BBox) -> float:
    left = max(face[0], person[0])
    top = max(face[1], person[1])
    right = min(face[2], person[2])
    bottom = min(face[3], person[3])
    intersection = max(0, right - left) * max(0, bottom - top)
    return intersection / max(1, _area(face))


def associate_identities_to_people(
    people: Sequence[BBox],
    identities: Sequence[TrackedIdentity],
    *,
    minimum_face_coverage: float,
) -> tuple[PersonIdentity, ...]:
    """얼굴 중심·포함률로 일대일 연결하며 모호한 연결은 모두 버린다."""
    candidates_by_face: list[list[int]] = []
    for identity in identities:
        center_x = (identity.bbox[0] + identity.bbox[2]) / 2.0
        center_y = (identity.bbox[1] + identity.bbox[3]) / 2.0
        candidates_by_face.append(
            [
                index
                for index, person in enumerate(people)
                if person[0] <= center_x <= person[2]
                and person[1] <= center_y <= person[3]
                and _face_coverage(identity.bbox, person) >= minimum_face_coverage
            ]
        )

    unique_owner_counts = Counter(
        candidates[0] for candidates in candidates_by_face if len(candidates) == 1
    )
    matches: list[PersonIdentity] = []
    for identity, candidates in zip(identities, candidates_by_face, strict=True):
        if len(candidates) != 1 or unique_owner_counts[candidates[0]] != 1:
            continue
        is_registered = (
            identity.status is IdentityStatus.REGISTERED
            and identity.student_id is not None
        )
        matches.append(
            PersonIdentity(
                person_index=candidates[0],
                face_bbox=identity.bbox,
                track_id=identity.track_id,
                student_id=identity.student_id if is_registered else None,
                similarity=identity.similarity if is_registered else None,
            )
        )
    return tuple(matches)


class FaceIdentificationRuntime:
    """모델은 한 번 로딩하고 갤러리만 주기적으로 원자적으로 교체한다."""

    def __init__(
        self,
        *,
        detector: Any,
        recognizer: Any,
        gallery_loader: FaceGalleryLoader,
        config: FaceIdentificationConfig,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._detector = detector
        self._recognizer = recognizer
        self._gallery_loader = gallery_loader
        self._config = config
        self._clock = clock
        self._engine: FaceIdentityEngine | None = None
        self._gallery_revision: GalleryRevision | None = None
        self._gallery_loaded_at: float | None = None
        self._trackers: dict[str, MultiFaceIdentityTracker] = {}

    def _refresh_gallery(self) -> FaceIdentityEngine:
        now = self._clock()
        if (
            self._engine is not None
            and self._gallery_loaded_at is not None
            and now - self._gallery_loaded_at < self._config.gallery_refresh_seconds
        ):
            return self._engine

        snapshot = self._gallery_loader.load()
        self._gallery_loaded_at = now
        if self._engine is not None and snapshot.revision == self._gallery_revision:
            return self._engine

        try:
            gallery = FaceGallery.from_entries(snapshot.entries)
        except ValueError:
            raise FaceGalleryUnavailable(
                "얼굴 갤러리 구성이 올바르지 않습니다."
            ) from None
        self._engine = FaceIdentityEngine(
            detector=self._detector,
            recognizer=self._recognizer,
            gallery=gallery,
            thresholds=IdentityThresholds(
                self._config.similarity_threshold,
                self._config.margin_threshold,
            ),
            detection_threshold=self._config.detection_threshold,
            identity_min_detection_confidence=(
                self._config.identity_min_detection_confidence
            ),
            minimum_face_size=self._config.minimum_face_size,
            preferred_face_size=self._config.preferred_face_size,
            minimum_blur_score=self._config.minimum_blur_score,
            preferred_blur_score=self._config.preferred_blur_score,
            uncertain_quality_threshold=self._config.uncertain_quality_threshold,
            use_flip_tta=self._config.use_flip_tta,
            tta_similarity_band=self._config.tta_similarity_band,
            tta_margin_band=self._config.tta_margin_band,
        )
        self._gallery_revision = snapshot.revision
        # 학생 추가·수정·삭제 뒤에는 이전 갤러리에서 쌓은 증거를 재사용하지 않는다.
        self._trackers.clear()
        return self._engine

    def identify(
        self,
        *,
        camera_id: str,
        image_bgr: np.ndarray,
        person_bboxes: Sequence[BBox],
    ) -> tuple[PersonIdentity, ...]:
        engine = self._refresh_gallery()
        faces = engine.identify(image_bgr)
        tracker = self._trackers.get(camera_id)
        if tracker is None:
            tracker = MultiFaceIdentityTracker(
                engine,
                history_size=self._config.tracker_history_size,
                minimum_observations=self._config.tracker_minimum_observations,
                stale_frames=self._config.tracker_stale_frames,
            )
            self._trackers[camera_id] = tracker
        identities = tracker.update(faces)
        return associate_identities_to_people(
            person_bboxes,
            identities,
            minimum_face_coverage=self._config.minimum_face_coverage,
        )
