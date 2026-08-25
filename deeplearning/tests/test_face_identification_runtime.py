from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import numpy as np
import pytest
from bson import ObjectId

from deeplearning.face_identification import (
    FaceGallerySnapshot,
    FaceGalleryUnavailable,
    FaceIdentificationConfig,
    FaceIdentificationRuntime,
    FaceModelMetadata,
    MongoFaceGalleryLoader,
)
from deeplearning.face_identity import GalleryEntry


def _vector(index: int) -> np.ndarray:
    vector = np.zeros(512, dtype=np.float32)
    vector[index] = 1.0
    return vector


class _FakeAdmin:
    def command(self, name: str) -> None:
        assert name == "ping"


class _FakeCollection:
    def __init__(self, name: str, documents: list[dict[str, Any]]) -> None:
        self._name = name
        self._documents = documents

    def find(
        self, query: dict[str, Any], projection: dict[str, int]
    ) -> list[dict[str, Any]]:
        if self._name == "students":
            assert query == {"is_active": True, "face_registered": True}
            assert projection["_id"] == 1
        else:
            assert query == {}
            assert projection["vector"] == 1
        return self._documents


class _FakeDatabase:
    def __init__(
        self,
        documents: list[dict[str, Any]],
        students: list[dict[str, Any]],
    ) -> None:
        self._documents = documents
        self._students = students

    def __getitem__(self, name: str) -> _FakeCollection:
        assert name in {"face_embeddings", "students"}
        return _FakeCollection(
            name, self._students if name == "students" else self._documents
        )


class _FakeClient:
    def __init__(
        self,
        documents: list[dict[str, Any]],
        students: list[dict[str, Any]] | None = None,
    ) -> None:
        self.admin = _FakeAdmin()
        self._documents = documents
        self._students = (
            students
            if students is not None
            else [
                {
                    "_id": "student-a",
                    "is_active": True,
                    "face_registered": True,
                }
            ]
        )
        self.closed = False

    def __getitem__(self, name: str) -> _FakeDatabase:
        assert name == "classroom"
        return _FakeDatabase(self._documents, self._students)

    def close(self) -> None:
        self.closed = True


def _gallery_document(vector: np.ndarray) -> dict[str, Any]:
    return {
        "student_id": "student-a",
        "vector": vector.tolist(),
        "dimension": 512,
        "normalized": True,
        "model_name": "arcface",
        "model_version": "model-v1",
        "preprocessing_version": "crop-v1",
        "updated_at": datetime(2026, 8, 22, tzinfo=UTC),
    }


def test_Mongo_갤러리는_FastAPI_대표_embedding_계약을_그대로_읽는다() -> None:
    client = _FakeClient([_gallery_document(_vector(0))])
    factory_arguments: dict[str, Any] = {}

    def factory(database_url: str, **kwargs: Any) -> _FakeClient:
        factory_arguments.update(database_url=database_url, **kwargs)
        return client

    loader = MongoFaceGalleryLoader(
        database_url="mongodb://example.invalid",
        database_name="classroom",
        collection_name="face_embeddings",
        expected_metadata=FaceModelMetadata("arcface", "model-v1", "crop-v1"),
        client_factory=factory,
    )

    snapshot = loader.load()

    assert snapshot.entries[0].student_id == "student-a"
    assert snapshot.revision == (("student-a", "2026-08-22T00:00:00+00:00"),)
    assert snapshot.excluded_entries == 0
    assert factory_arguments["tz_aware"] is True
    assert client.closed is True


def test_Mongo_갤러리는_정규화_표시와_실제_벡터가_다르면_거부한다() -> None:
    client = _FakeClient([_gallery_document(_vector(0) * 2)])
    loader = MongoFaceGalleryLoader(
        database_url="mongodb://example.invalid",
        database_name="classroom",
        collection_name="face_embeddings",
        expected_metadata=FaceModelMetadata("arcface", "model-v1", "crop-v1"),
        client_factory=lambda *_args, **_kwargs: client,
    )

    with pytest.raises(FaceGalleryUnavailable):
        loader.load()

    assert client.closed is True


def test_Mongo_갤러리는_활성_얼굴등록_학생이_아닌_embedding을_제외한다() -> None:
    active = _gallery_document(_vector(0))
    inactive = {**_gallery_document(_vector(1)), "student_id": "student-old"}
    client = _FakeClient(
        [active, inactive],
        students=[{"_id": "student-a", "is_active": True, "face_registered": True}],
    )
    loader = MongoFaceGalleryLoader(
        database_url="mongodb://example.invalid",
        database_name="classroom",
        collection_name="face_embeddings",
        expected_metadata=FaceModelMetadata("arcface", "model-v1", "crop-v1"),
        client_factory=lambda *_args, **_kwargs: client,
    )

    snapshot = loader.load()

    assert [entry.student_id for entry in snapshot.entries] == ["student-a"]
    assert snapshot.excluded_entries == 1


def test_Mongo_갤러리는_ObjectId_학생을_문자열_embedding과_연결한다() -> None:
    student_id = ObjectId()
    document = {
        **_gallery_document(_vector(0)),
        "student_id": str(student_id),
    }
    client = _FakeClient(
        [document],
        students=[{"_id": student_id, "is_active": True, "face_registered": True}],
    )
    loader = MongoFaceGalleryLoader(
        database_url="mongodb://example.invalid",
        database_name="classroom",
        collection_name="face_embeddings",
        expected_metadata=FaceModelMetadata("arcface", "model-v1", "crop-v1"),
        client_factory=lambda *_args, **_kwargs: client,
    )

    snapshot = loader.load()

    assert [entry.student_id for entry in snapshot.entries] == [str(student_id)]


def test_Mongo_갤러리는_embedding이_없는_ObjectId_학생때문에_실패하지_않는다() -> None:
    client = _FakeClient(
        [_gallery_document(_vector(0))],
        students=[
            {"_id": ObjectId(), "is_active": True, "face_registered": True},
            {"_id": "student-a", "is_active": True, "face_registered": True},
        ],
    )
    loader = MongoFaceGalleryLoader(
        database_url="mongodb://example.invalid",
        database_name="classroom",
        collection_name="face_embeddings",
        expected_metadata=FaceModelMetadata("arcface", "model-v1", "crop-v1"),
        client_factory=lambda *_args, **_kwargs: client,
    )

    snapshot = loader.load()

    assert [entry.student_id for entry in snapshot.entries] == ["student-a"]


def test_Mongo_갤러리는_유효한_활성_학생_embedding이_없으면_거부한다() -> None:
    client = _FakeClient([_gallery_document(_vector(0))], students=[])
    loader = MongoFaceGalleryLoader(
        database_url="mongodb://example.invalid",
        database_name="classroom",
        collection_name="face_embeddings",
        expected_metadata=FaceModelMetadata("arcface", "model-v1", "crop-v1"),
        client_factory=lambda *_args, **_kwargs: client,
    )

    with pytest.raises(FaceGalleryUnavailable, match="비어"):
        loader.load()


class GalleryLoader:
    def __init__(self) -> None:
        self.load_count = 0
        self.revision = "1"

    def load(self) -> FaceGallerySnapshot:
        self.load_count += 1
        return FaceGallerySnapshot(
            entries=(
                GalleryEntry("student-a", _vector(0)),
                GalleryEntry("student-b", _vector(1)),
            ),
            revision=(("student-a", self.revision), ("student-b", self.revision)),
        )


class Detector:
    def detect(
        self, image: np.ndarray, *, max_num: int
    ) -> tuple[np.ndarray, np.ndarray]:
        del image, max_num
        return (
            np.asarray([[30, 20, 80, 75, 0.95]], dtype=np.float32),
            np.asarray(
                [[[40, 35], [65, 35], [52, 47], [42, 62], [63, 62]]],
                dtype=np.float32,
            ),
        )


class Recognizer:
    def get_feat(self, image: np.ndarray) -> np.ndarray:
        del image
        return _vector(0).reshape(1, -1)


def runtime(loader: GalleryLoader, clock: list[float]) -> FaceIdentificationRuntime:
    return FaceIdentificationRuntime(
        detector=Detector(),
        recognizer=Recognizer(),
        gallery_loader=loader,
        config=FaceIdentificationConfig(
            similarity_threshold=0.5,
            margin_threshold=0.1,
            track_similarity_threshold=0.5,
            gallery_refresh_seconds=10,
            minimum_face_size=1,
            preferred_face_size=2,
            minimum_blur_score=0,
            preferred_blur_score=1,
            uncertain_quality_threshold=0,
            tracker_minimum_observations=1,
        ),
        clock=lambda: clock[0],
    )


def test_갤러리_학생을_얼굴_track으로_반환한다() -> None:
    loader = GalleryLoader()
    active = runtime(loader, [0.0])

    identities = active.identify(
        camera_id="entry-camera",
        image_bgr=np.zeros((120, 160, 3), dtype=np.uint8),
    )

    assert len(identities) == 1
    assert identities[0].bbox == (30, 20, 80, 75)
    assert identities[0].student_id == "student-a"
    assert identities[0].similarity == 1.0


def test_갤러리_revision이_같으면_모델과_track을_유지한다() -> None:
    loader = GalleryLoader()
    clock = [0.0]
    active = runtime(loader, clock)
    frame = np.zeros((120, 160, 3), dtype=np.uint8)

    first = active.identify(camera_id="entry-camera", image_bgr=frame)[0]
    clock[0] = 11.0
    second = active.identify(camera_id="entry-camera", image_bgr=frame)[0]

    assert loader.load_count == 2
    assert second.track_id == first.track_id


def test_readiness가_첫_식별_전에_갤러리를_검증하고_주기_안에는_재사용한다() -> None:
    loader = GalleryLoader()
    active = runtime(loader, [0.0])

    active.ensure_ready()
    active.ensure_ready()

    assert loader.load_count == 1


def test_잘못된_갤러리는_refresh_시간을_갱신하지_않아_즉시_재시도한다() -> None:
    class RecoveringLoader(GalleryLoader):
        def load(self) -> FaceGallerySnapshot:
            self.load_count += 1
            if self.load_count == 1:
                return FaceGallerySnapshot(entries=(), revision=())
            return FaceGallerySnapshot(
                entries=(GalleryEntry("student-a", _vector(0)),),
                revision=(("student-a", "2"),),
            )

    loader = RecoveringLoader()
    active = runtime(loader, [0.0])

    with pytest.raises(FaceGalleryUnavailable):
        active.ensure_ready()
    active.ensure_ready()

    assert loader.load_count == 2
