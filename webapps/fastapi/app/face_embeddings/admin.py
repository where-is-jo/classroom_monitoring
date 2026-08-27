"""모델별 얼굴 갤러리를 준비하는 개인정보 비노출 관리자 CLI.

기본 동작은 dry-run이다. 실제 MongoDB 쓰기는 각 하위 명령의 ``--apply``를 지정한
경우에만 수행하며, 출력에는 학생 ID·이름·학번·경로를 포함하지 않는다.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from bson import ObjectId
from bson.errors import InvalidId

from ..shared.config import Settings
from ..shared.database import create_mongo_client, select_database
from .adapters.http_analyzer import HttpFaceEmbeddingAnalyzer
from .adapters.mongo import MongoFaceEmbeddingRepository, _to_domain
from .errors import FaceEmbeddingInputError
from .models import FaceEmbedding
from .ports import FaceEmbeddingAnalyzer, FaceEmbeddingRepository
from .service import MAX_SOURCE_SAMPLES, build_face_embedding, select_evenly

ARCFACE_METADATA = (
    "arcface",
    "insightface-buffalo_l-w600k_r50-v0.7",
    "insightface-norm-crop-112-v1",
)
ADAFACE_METADATA = (
    "adaface",
    "cvlface-adaface-ir50-webface4m-fe7718c6",
    "cvlface-rgb-norm-crop-112-v1",
)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass(frozen=True)
class ManifestStudent:
    student_id: str
    image_dir: Path


@dataclass(frozen=True)
class GalleryManifest:
    schema_version: int
    students: tuple[ManifestStudent, ...]


@dataclass(frozen=True)
class MigrationResult:
    candidates: int
    already_present: int
    copied: int
    applied: bool


@dataclass(frozen=True)
class GalleryBuildResult:
    students: int
    source_images: int
    selected_images: int
    created: int
    updated: int
    applied: bool


def load_manifest(path: Path) -> GalleryManifest:
    """schema v1 manifest를 읽고 모든 이미지 디렉터리가 절대 경로인지 확인한다."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("manifest를 JSON으로 읽을 수 없습니다.") from error
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "students"}:
        raise ValueError("manifest 최상위 형식이 올바르지 않습니다.")
    if raw["schema_version"] != 1 or not isinstance(raw["students"], list):
        raise ValueError("manifest schema_version은 1이어야 합니다.")

    students: list[ManifestStudent] = []
    seen_ids: set[str] = set()
    for value in raw["students"]:
        if not isinstance(value, dict) or set(value) != {"student_id", "image_dir"}:
            raise ValueError("manifest 학생 항목 형식이 올바르지 않습니다.")
        student_id = value["student_id"]
        image_dir_value = value["image_dir"]
        if (
            not isinstance(student_id, str)
            or not student_id.strip()
            or student_id in seen_ids
            or not isinstance(image_dir_value, str)
        ):
            raise ValueError("manifest 학생 식별자가 올바르지 않습니다.")
        image_dir = Path(image_dir_value)
        if not image_dir.is_absolute() or not image_dir.is_dir():
            raise ValueError("manifest image_dir은 존재하는 절대 디렉터리여야 합니다.")
        seen_ids.add(student_id)
        students.append(ManifestStudent(student_id, image_dir))
    if not students:
        raise ValueError("manifest에는 학생 항목이 하나 이상 필요합니다.")
    return GalleryManifest(schema_version=1, students=tuple(students))


def migrate_legacy_arcface_gallery(
    database: Any,
    repository: FaceEmbeddingRepository,
    *,
    apply: bool,
    id_factory: Callable[[], str] = lambda: str(uuid4()),
) -> MigrationResult:
    """레거시 컬렉션을 현재 학생 원장에 연결해 ArcFace 컬렉션으로 멱등 복사한다.

    학생 문서를 다시 만든 환경에서는 생체 벡터의 ``student_id``만 고아가 될 수 있다.
    원본 ID가 없을 때에만 유일한 ``student_number + name`` 일치를 허용한다. 두 값 중
    하나라도 다르거나 후보가 여럿이면 잘못된 사람에게 벡터를 붙이지 않고 중단한다.
    """

    documents = list(database[MongoFaceEmbeddingRepository.legacy_collection_name].find({}))
    candidates: list[FaceEmbedding] = []
    current_student_ids: set[str] = set()
    for document in documents:
        embedding = _to_domain(document)
        metadata = (
            embedding.model_name,
            embedding.model_version,
            embedding.preprocessing_version,
        )
        if metadata != ARCFACE_METADATA:
            raise RuntimeError("레거시 갤러리에 ArcFace 계약과 다른 항목이 있습니다.")
        student = _resolve_legacy_student(database, embedding)
        current_student_id = str(student["_id"])
        if current_student_id in current_student_ids:
            raise RuntimeError("레거시 얼굴 벡터가 같은 현재 학생에게 중복 연결됩니다.")
        current_student_ids.add(current_student_id)
        candidates.append(
            replace(
                embedding,
                id=(embedding.id if current_student_id == embedding.student_id else id_factory()),
                student_id=current_student_id,
                student_name=_required_string(student, "name"),
                student_number=_required_string(student, "student_number"),
            )
        )

    missing: list[FaceEmbedding] = []
    already_present = 0
    for embedding in candidates:
        existing = repository.find_by_student(embedding.student_id, "arcface")
        if existing is None:
            missing.append(embedding)
        else:
            already_present += 1
    if apply:
        for embedding in missing:
            repository.save(embedding)
    return MigrationResult(
        candidates=len(candidates),
        already_present=already_present,
        copied=len(missing),
        applied=apply,
    )


def _resolve_legacy_student(database: Any, embedding: FaceEmbedding) -> dict[str, Any]:
    direct = _find_student_by_id(database, embedding.student_id)
    if direct is not None:
        _validate_legacy_student_match(direct, embedding)
        return direct

    matches = list(
        database["students"].find(
            {
                "student_number": embedding.student_number,
                "name": embedding.student_name,
                "is_active": True,
                "face_registered": True,
            },
            {
                "_id": 1,
                "name": 1,
                "student_number": 1,
                "is_active": 1,
                "face_registered": 1,
            },
        )
    )
    if len(matches) != 1:
        raise RuntimeError("레거시 얼굴 벡터를 현재 학생 원장에 유일하게 연결할 수 없습니다.")
    return cast(dict[str, Any], matches[0])


def _find_student_by_id(database: Any, student_id: str) -> dict[str, Any] | None:
    candidates: list[str | ObjectId] = [student_id]
    with suppress(InvalidId):
        candidates.append(ObjectId(student_id))
    for candidate in candidates:
        document = database["students"].find_one(
            {"_id": candidate},
            {
                "_id": 1,
                "name": 1,
                "student_number": 1,
                "is_active": 1,
                "face_registered": 1,
            },
        )
        if document is not None:
            return cast(dict[str, Any], document)
    return None


def _validate_legacy_student_match(student: dict[str, Any], embedding: FaceEmbedding) -> None:
    if (
        student.get("is_active") is not True
        or student.get("face_registered") is not True
        or student.get("student_number") != embedding.student_number
        or student.get("name") != embedding.student_name
    ):
        raise RuntimeError("레거시 얼굴 벡터와 현재 학생 원장이 일치하지 않습니다.")


def build_adaface_gallery(
    database: Any,
    repository: FaceEmbeddingRepository,
    analyzer: FaceEmbeddingAnalyzer,
    manifest: GalleryManifest,
    *,
    apply: bool,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    id_factory: Callable[[], str] = lambda: str(uuid4()),
) -> GalleryBuildResult:
    """외부 원본 폴더를 분석해 AdaFace 대표 embedding을 일괄 준비한다."""

    built: list[FaceEmbedding] = []
    source_images = 0
    selected_images = 0
    for item in manifest.students:
        student = _find_active_registered_student(database, item.student_id)
        image_paths = sorted(
            path
            for path in item.image_dir.iterdir()
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        )
        source_images += len(image_paths)
        selected_paths = select_evenly(image_paths, MAX_SOURCE_SAMPLES)
        selected_images += len(selected_paths)
        try:
            images = [path.read_bytes() for path in selected_paths]
        except OSError as error:
            raise ValueError("manifest 이미지 파일을 읽을 수 없습니다.") from error
        embedding = build_face_embedding(
            student_id=str(student["_id"]),
            student_name=_required_string(student, "name"),
            student_number=_required_string(student, "student_number"),
            enrollment_id="external-manifest-v1",
            images=images,
            analyzer=analyzer,
            now=clock(),
            embedding_id=id_factory(),
        )
        metadata = (
            embedding.model_name,
            embedding.model_version,
            embedding.preprocessing_version,
        )
        if metadata != ADAFACE_METADATA:
            raise FaceEmbeddingInputError(
                "분석 서버가 선택한 AdaFace 모델 metadata를 반환하지 않았습니다."
            )
        built.append(embedding)

    created = 0
    updated = 0
    for embedding in built:
        existing = repository.find_by_student(embedding.student_id, "adaface")
        if existing is None:
            created += 1
        else:
            updated += 1
    if apply:
        for embedding in built:
            repository.save(embedding)
    return GalleryBuildResult(
        students=len(built),
        source_images=source_images,
        selected_images=selected_images,
        created=created,
        updated=updated,
        applied=apply,
    )


def _find_active_registered_student(database: Any, student_id: str) -> dict[str, Any]:
    candidates: list[str | ObjectId] = [student_id]
    with suppress(InvalidId):
        candidates.append(ObjectId(student_id))
    for candidate in candidates:
        document = database["students"].find_one(
            {"_id": candidate},
            {
                "_id": 1,
                "name": 1,
                "student_number": 1,
                "is_active": 1,
                "face_registered": 1,
            },
        )
        if document is not None:
            if document.get("is_active") is not True or document.get("face_registered") is not True:
                break
            return cast(dict[str, Any], document)
    raise ValueError("manifest 학생이 활성 얼굴 등록 상태가 아닙니다.")


def _required_string(document: dict[str, Any], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("학생 원장 필수 문자열이 올바르지 않습니다.")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        help="현재 프로세스에만 주입할 FastAPI env 파일",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    migrate = subparsers.add_parser(
        "migrate-legacy-arcface",
        aliases=["migrate-arcface-gallery"],
        help="레거시 face_embeddings를 ArcFace 전용 컬렉션으로 복사",
    )
    migrate.add_argument("--apply", action="store_true")
    build = subparsers.add_parser(
        "build-adaface-gallery",
        aliases=["build-gallery"],
        help="외부 manifest의 이미지로 AdaFace 갤러리 생성",
    )
    build.add_argument(
        "--model",
        choices=("adaface",),
        default="adaface",
        help="현재 외부 갤러리 생성에서 지원하는 인식 모델",
    )
    build.add_argument("--manifest", type=Path, required=True)
    build.add_argument("--analyzer-url")
    build.add_argument("--apply", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.env_file is not None:
        from dotenv import dotenv_values

        # 비밀번호의 ``$``를 변수 치환으로 해석하면 유효한 URL이 조용히 바뀐다.
        values = dotenv_values(args.env_file, interpolate=False)
        for key, value in values.items():
            if value is not None:
                os.environ[key] = value
        # GPU의 deeplearning env는 같은 MongoDB를 읽기 전용 FACE_GALLERY_* 이름으로
        # 가진다. 관리자 CLI에서도 비밀값을 복사해 새 파일을 만들지 않고 재사용한다.
        gallery_url = values.get("FACE_GALLERY_DATABASE_URL")
        gallery_name = values.get("FACE_GALLERY_DATABASE_NAME")
        if gallery_url and gallery_name:
            os.environ["DATABASE_MODE"] = "mongodb"
            os.environ["DATABASE_URL"] = gallery_url
            os.environ["DATABASE_NAME"] = gallery_name
    settings = Settings()
    if (
        settings.database_mode != "mongodb"
        or settings.database_url is None
        or settings.database_name is None
    ):
        raise RuntimeError("관리자 CLI는 MongoDB 설정이 필요합니다.")
    client = create_mongo_client(
        settings.database_url,
        timeout_seconds=settings.database_connect_timeout_seconds,
    )
    try:
        database = select_database(client, settings.database_name)
        repository = MongoFaceEmbeddingRepository(database)
        if args.apply:
            repository.ensure_indexes(database)
        if args.command in {"migrate-legacy-arcface", "migrate-arcface-gallery"}:
            migration_result = migrate_legacy_arcface_gallery(
                database, repository, apply=args.apply
            )
            print(
                "레거시 ArcFace 점검 완료: "
                f"후보={migration_result.candidates}, "
                f"기존={migration_result.already_present}, "
                f"복사대상={migration_result.copied}, "
                f"적용={migration_result.applied}"
            )
        else:
            manifest = load_manifest(args.manifest.resolve())
            analyzer = HttpFaceEmbeddingAnalyzer(
                args.analyzer_url or settings.face_analyzer_url,
                settings.face_analyzer_timeout_seconds,
            )
            build_result = build_adaface_gallery(
                database,
                repository,
                analyzer,
                manifest,
                apply=args.apply,
            )
            print(
                "AdaFace 갤러리 점검 완료: "
                f"학생={build_result.students}, 원본={build_result.source_images}, "
                f"선택={build_result.selected_images}, 신규={build_result.created}, "
                f"갱신={build_result.updated}, 적용={build_result.applied}"
            )
    finally:
        client.close()
    print("학생 식별자·이름·학번·이미지 경로는 출력하지 않았습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
