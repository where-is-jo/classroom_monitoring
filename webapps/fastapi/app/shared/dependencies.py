"""잔존 저장소와 서비스의 단일 조립 지점."""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from fastapi import Depends
from pymongo import MongoClient

from ..classrooms.adapters.memory_repository import InMemoryClassroomRepository
from ..classrooms.adapters.mongo_repository import MongoClassroomRepository
from ..classrooms.ports import ClassroomRepository
from ..classrooms.service import ClassroomService
from ..demo_seed import seed_demo_data
from ..face_enrollment.adapters.http_analyzer import HttpFaceAnalyzer
from ..face_enrollment.adapters.local_storage import LocalFaceObjectStorage
from ..face_enrollment.adapters.memory import (
    InMemoryFaceEnrollmentRepository,
    InMemoryFaceObjectStorage,
    SyntheticFaceAnalyzer,
)
from ..face_enrollment.models import PoseBin
from ..face_enrollment.rules import EnrollmentThresholds
from ..face_enrollment.service import FaceEnrollmentService
from ..video_monitoring.service import VideoDemoService
from .config import Settings
from .database import (
    DatabaseOperationError,
    MongoDatabase,
    MongoDocument,
    create_mongo_client,
    initialize_indexes,
    ping_database,
    select_database,
)
from .errors import DatabaseUnavailableError


def utc_now() -> datetime:
    return datetime.now(UTC)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


@lru_cache
def _classroom_repository() -> InMemoryClassroomRepository:
    return InMemoryClassroomRepository()


@lru_cache
def _mongo_client() -> MongoClient[MongoDocument]:
    settings = get_settings()
    if settings.database_url is None:
        raise DatabaseOperationError("DATABASE_URL이 필요합니다.")
    return create_mongo_client(
        settings.database_url,
        timeout_seconds=settings.database_connect_timeout_seconds,
    )


@lru_cache
def _mongo_database() -> MongoDatabase:
    settings = get_settings()
    if settings.database_name is None:
        raise DatabaseOperationError("DATABASE_NAME이 필요합니다.")
    return select_database(_mongo_client(), settings.database_name)


@lru_cache
def _mongo_classroom_repository() -> MongoClassroomRepository:
    return MongoClassroomRepository(_mongo_database())


def get_classroom_repository(
    settings: Settings = Depends(get_settings),
) -> ClassroomRepository:
    if settings.database_mode == "memory":
        return _classroom_repository()
    return _mongo_classroom_repository()


def get_classroom_service(
    repository: ClassroomRepository = Depends(get_classroom_repository),
    settings: Settings = Depends(get_settings),
) -> ClassroomService:
    return ClassroomService(
        repository,
        occupancy_confidence_threshold=settings.seat_occupancy_confidence_threshold,
        clock=utc_now,
    )


def get_video_demo_service(
    settings: Settings = Depends(get_settings),
) -> VideoDemoService:
    if settings.demo_mode_enabled and settings.app_env in {"local", "dev"}:
        return VideoDemoService(clock=utc_now)
    return VideoDemoService(streams=(), clips=(), clock=utc_now)


@lru_cache
def _face_enrollment_repository() -> InMemoryFaceEnrollmentRepository:
    return InMemoryFaceEnrollmentRepository()


@lru_cache
def _memory_face_object_storage() -> InMemoryFaceObjectStorage:
    return InMemoryFaceObjectStorage()


@lru_cache
def _local_face_object_storage(storage_dir: str) -> LocalFaceObjectStorage:
    return LocalFaceObjectStorage(Path(storage_dir))


def get_face_object_storage(
    settings: Settings,
) -> InMemoryFaceObjectStorage | LocalFaceObjectStorage:
    if settings.face_local_sample_storage_enabled:
        return _local_face_object_storage(str(settings.face_local_sample_storage_dir))
    return _memory_face_object_storage()


@lru_cache
def _face_analyzer(pose_run_lengths: tuple[tuple[PoseBin, int], ...]) -> SyntheticFaceAnalyzer:
    return SyntheticFaceAnalyzer(pose_run_lengths)


@lru_cache
def _http_face_analyzer(base_url: str, timeout_seconds: float) -> HttpFaceAnalyzer:
    return HttpFaceAnalyzer(base_url, timeout_seconds)


def get_face_enrollment_service(
    settings: Settings = Depends(get_settings),
) -> FaceEnrollmentService:
    pose_quotas = {
        PoseBin.FRONT: settings.face_pose_front_quota,
        PoseBin.LEFT: settings.face_pose_left_quota,
        PoseBin.RIGHT: settings.face_pose_right_quota,
        PoseBin.UP: settings.face_pose_up_quota,
        PoseBin.DOWN: settings.face_pose_down_quota,
    }
    return FaceEnrollmentService(
        _face_enrollment_repository(),
        get_face_object_storage(settings),
        (
            _http_face_analyzer(settings.face_analyzer_url, settings.face_analyzer_timeout_seconds)
            if settings.face_analyzer_mode == "http"
            else _face_analyzer(tuple(pose_quotas.items()))
        ),
        required_sample_count=settings.face_enrollment_required_samples,
        pose_quotas=pose_quotas,
        thresholds=EnrollmentThresholds(
            detection_confidence_min=settings.face_detection_confidence_min,
            face_size_ratio_min=settings.face_size_ratio_min,
            roll_degrees_max=settings.face_roll_degrees_max,
            blur_score_min=settings.face_blur_score_min,
            brightness_score_min=settings.face_brightness_score_min,
            landmark_confidence_min=settings.face_landmark_confidence_min,
            occlusion_score_max=settings.face_occlusion_score_max,
            duplicate_score_max=settings.face_duplicate_score_max,
            motion_speed_dps_max=settings.face_motion_speed_dps_max,
            yaw_side_degrees=settings.face_yaw_side_degrees,
            pitch_side_degrees=settings.face_pitch_side_degrees,
        ),
        clock=utc_now,
    )


def initialize_data_store() -> None:
    settings = get_settings()
    if settings.database_mode == "mongodb":
        database = _mongo_database()
        ping_database(database)
        initialize_indexes(database, [MongoClassroomRepository.ensure_indexes])
        return
    if settings.demo_mode_enabled:
        seed_demo_data(
            get_classroom_service(get_classroom_repository(settings), settings), now=utc_now()
        )


def close_data_store() -> None:
    if _mongo_client.cache_info().currsize:
        _mongo_client().close()
    _mongo_classroom_repository.cache_clear()
    _mongo_database.cache_clear()
    _mongo_client.cache_clear()
    _face_enrollment_repository.cache_clear()
    _memory_face_object_storage.cache_clear()
    _local_face_object_storage.cache_clear()
    _face_analyzer.cache_clear()
    _http_face_analyzer.cache_clear()


def verify_readiness(settings: Settings = Depends(get_settings)) -> None:
    if settings.database_mode == "memory":
        return
    try:
        ping_database(_mongo_database())
    except DatabaseOperationError:
        raise DatabaseUnavailableError() from None
