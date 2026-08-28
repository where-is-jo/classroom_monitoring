"""얼굴 갤러리 관리자 CLI의 dry-run·manifest·멱등성 계약."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.face_embeddings.adapters.memory import InMemoryFaceEmbeddingRepository
from app.face_embeddings.admin import (
    GalleryManifest,
    ManifestStudent,
    _parser,
    build_adaface_gallery,
    load_manifest,
    migrate_legacy_arcface_gallery,
)
from app.face_embeddings.models import SampleEmbedding

NOW = datetime(2026, 8, 25, 1, 0, tzinfo=UTC)


class Collection:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = documents

    def find(
        self,
        query: dict[str, object],
        projection: dict[str, int] | None = None,
    ) -> list[dict[str, Any]]:
        del projection
        return [
            value
            for value in self.documents
            if all(value.get(key) == expected for key, expected in query.items())
        ]

    def find_one(
        self, query: dict[str, object], projection: dict[str, int]
    ) -> dict[str, Any] | None:
        assert projection["face_registered"] == 1
        return next(
            (value for value in self.documents if value["_id"] == query["_id"]),
            None,
        )


class Database:
    def __init__(
        self,
        *,
        legacy: list[dict[str, Any]] | None = None,
        students: list[dict[str, Any]] | None = None,
    ) -> None:
        self.collections = {
            "face_embeddings": Collection(legacy or []),
            "students": Collection(students or []),
        }

    def __getitem__(self, name: str) -> Collection:
        return self.collections[name]


class AdaFaceAnalyzer:
    def __init__(self) -> None:
        self.calls = 0

    def create(self, image: bytes) -> SampleEmbedding:
        assert image.startswith(b"image-")
        self.calls += 1
        return SampleEmbedding(
            vector=(1.0,) + (0.0,) * 511,
            dimension=512,
            normalized=True,
            model_name="adaface",
            model_version="cvlface-adaface-ir50-webface4m-fe7718c6",
            preprocessing_version="cvlface-rgb-norm-crop-112-v1",
        )


def _legacy_document() -> dict[str, Any]:
    return {
        "_id": "embedding-arcface",
        "student_id": "student-01",
        "student_name": "테스트 학생",
        "student_number": "ST-001",
        "enrollment_id": "enrollment-01",
        "vector": [1.0] + [0.0] * 511,
        "dimension": 512,
        "normalized": True,
        "model_name": "arcface",
        "model_version": "insightface-buffalo_l-w600k_r50-v0.7",
        "preprocessing_version": "insightface-norm-crop-112-v1",
        "source_sample_count": 6,
        "created_at": NOW,
        "updated_at": NOW,
    }


def test_manifest는_schema_v1과_절대_이미지_경로를_요구한다(
    tmp_path: Path,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "students": [{"student_id": "student-01", "image_dir": str(image_dir)}],
            }
        ),
        encoding="utf-8",
    )

    manifest = load_manifest(manifest_path)

    assert manifest.students == (ManifestStudent("student-01", image_dir),)


def test_manifest의_상대_이미지_경로는_거부한다(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "students": [{"student_id": "student-01", "image_dir": "relative/images"}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="절대"):
        load_manifest(manifest_path)


def test_레거시_마이그레이션은_dry_run이_기본이고_적용은_멱등이다() -> None:
    database = Database(
        legacy=[_legacy_document()],
        students=[
            {
                "_id": "student-01",
                "name": "테스트 학생",
                "student_number": "ST-001",
                "is_active": True,
                "face_registered": True,
            }
        ],
    )
    repository = InMemoryFaceEmbeddingRepository()

    dry_run = migrate_legacy_arcface_gallery(
        database,
        repository,
        apply=False,
    )
    first_apply = migrate_legacy_arcface_gallery(
        database,
        repository,
        apply=True,
    )
    second_apply = migrate_legacy_arcface_gallery(
        database,
        repository,
        apply=True,
    )

    assert dry_run.copied == 1
    assert dry_run.applied is False
    assert first_apply.copied == 1
    assert first_apply.applied is True
    assert second_apply.copied == 0
    assert second_apply.already_present == 1


def test_레거시_ID가_고아면_유일한_학번과_이름으로_현재_학생에_연결한다() -> None:
    database = Database(
        legacy=[_legacy_document()],
        students=[
            {
                "_id": "current-student-01",
                "name": "테스트 학생",
                "student_number": "ST-001",
                "is_active": True,
                "face_registered": True,
            }
        ],
    )
    repository = InMemoryFaceEmbeddingRepository()

    result = migrate_legacy_arcface_gallery(
        database,
        repository,
        apply=True,
        id_factory=lambda: "remapped-embedding-01",
    )

    saved = repository.find_by_student("current-student-01", "arcface")
    assert result.copied == 1
    assert saved is not None
    assert saved.id == "remapped-embedding-01"
    assert repository.find_by_student("student-01", "arcface") is None


def test_레거시_ID와_학번_이름이_다르면_다른_학생에게_붙이지_않는다() -> None:
    database = Database(
        legacy=[_legacy_document()],
        students=[
            {
                "_id": "student-01",
                "name": "다른 학생",
                "student_number": "ST-999",
                "is_active": True,
                "face_registered": True,
            }
        ],
    )

    with pytest.raises(RuntimeError, match="일치하지 않습니다"):
        migrate_legacy_arcface_gallery(
            database,
            InMemoryFaceEmbeddingRepository(),
            apply=False,
        )


def test_AdaFace_갤러리는_최대_25장을_균등_선택하고_dry_run에는_저장하지_않는다(
    tmp_path: Path,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    for index in range(30):
        (image_dir / f"{index:02d}.jpg").write_bytes(f"image-{index}".encode())
    database = Database(
        students=[
            {
                "_id": "student-01",
                "name": "테스트 학생",
                "student_number": "ST-001",
                "is_active": True,
                "face_registered": True,
            }
        ]
    )
    repository = InMemoryFaceEmbeddingRepository()
    analyzer = AdaFaceAnalyzer()
    manifest = GalleryManifest(1, (ManifestStudent("student-01", image_dir),))

    result = build_adaface_gallery(
        database,
        repository,
        analyzer,
        manifest,
        apply=False,
        clock=lambda: NOW,
        id_factory=lambda: "embedding-adaface",
    )

    assert result.source_images == 30
    assert result.selected_images == 25
    assert result.created == 1
    assert result.applied is False
    assert analyzer.calls == 25
    assert repository.find_by_student("student-01", "adaface") is None


def test_AdaFace_갤러리는_apply일_때만_저장한다(tmp_path: Path) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    for index in range(5):
        (image_dir / f"{index}.jpg").write_bytes(f"image-{index}".encode())
    database = Database(
        students=[
            {
                "_id": "student-01",
                "name": "테스트 학생",
                "student_number": "ST-001",
                "is_active": True,
                "face_registered": True,
            }
        ]
    )
    repository = InMemoryFaceEmbeddingRepository()

    result = build_adaface_gallery(
        database,
        repository,
        AdaFaceAnalyzer(),
        GalleryManifest(1, (ManifestStudent("student-01", image_dir),)),
        apply=True,
        clock=lambda: NOW,
        id_factory=lambda: "embedding-adaface",
    )

    assert result.applied is True
    assert repository.find_by_student("student-01", "adaface") is not None


def test_AdaFace_갤러리_저장_중_실패해도_재실행으로_멱등_복구한다(
    tmp_path: Path,
) -> None:
    class FailSecondSaveOnceRepository(InMemoryFaceEmbeddingRepository):
        def __init__(self) -> None:
            super().__init__()
            self.save_calls = 0
            self.failed = False

        def save(self, embedding: Any) -> Any:
            self.save_calls += 1
            if self.save_calls == 2 and not self.failed:
                self.failed = True
                raise RuntimeError("temporary database failure")
            return super().save(embedding)

    students: list[dict[str, Any]] = []
    manifest_students: list[ManifestStudent] = []
    for student_index in range(2):
        student_id = f"student-{student_index + 1:02d}"
        image_dir = tmp_path / student_id
        image_dir.mkdir()
        for image_index in range(5):
            (image_dir / f"{image_index}.jpg").write_bytes(
                f"image-{student_index}-{image_index}".encode()
            )
        students.append(
            {
                "_id": student_id,
                "name": "테스트 학생",
                "student_number": f"ST-{student_index + 1:03d}",
                "is_active": True,
                "face_registered": True,
            }
        )
        manifest_students.append(ManifestStudent(student_id, image_dir))
    repository = FailSecondSaveOnceRepository()
    manifest = GalleryManifest(1, tuple(manifest_students))

    with pytest.raises(RuntimeError, match="temporary database failure"):
        build_adaface_gallery(
            Database(students=students),
            repository,
            AdaFaceAnalyzer(),
            manifest,
            apply=True,
            clock=lambda: NOW,
            id_factory=lambda: "embedding-adaface",
        )

    result = build_adaface_gallery(
        Database(students=students),
        repository,
        AdaFaceAnalyzer(),
        manifest,
        apply=True,
        clock=lambda: NOW,
        id_factory=lambda: "embedding-adaface",
    )

    assert result.created == 1
    assert result.updated == 1
    assert all(
        repository.find_by_student(item.student_id, "adaface") is not None
        for item in manifest.students
    )


def test_CLI의_apply는_명시하지_않으면_false다(tmp_path: Path) -> None:
    migrate = _parser().parse_args(
        ["--env-file", str(tmp_path / "fastapi.env"), "migrate-legacy-arcface"]
    )
    build = _parser().parse_args(
        ["build-adaface-gallery", "--manifest", str(tmp_path / "manifest.json")]
    )

    assert migrate.apply is False
    assert migrate.env_file == tmp_path / "fastapi.env"
    assert build.apply is False


def test_계획에_명시된_CLI_명령_별칭을_지원한다(tmp_path: Path) -> None:
    migrate = _parser().parse_args(["migrate-arcface-gallery"])
    build = _parser().parse_args(
        [
            "build-gallery",
            "--model",
            "adaface",
            "--manifest",
            str(tmp_path / "manifest.json"),
        ]
    )

    assert migrate.command == "migrate-arcface-gallery"
    assert build.command == "build-gallery"
    assert build.model == "adaface"
