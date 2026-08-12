"""Singleton repository and service assembly point."""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache

from fastapi import Depends
from pymongo import MongoClient

from ..classrooms.adapters.memory_repository import InMemoryClassroomRepository
from ..classrooms.adapters.mongo_repository import MongoClassroomRepository
from ..classrooms.ports import ClassroomRepository
from ..classrooms.service import ClassroomService
from ..demo_seed import seed_demo_data, seed_video_streams
from ..student_monitoring.adapters.memory_repository import (
    MemoryDetectionEventRepository,
    MemoryVideoSegmentRepository,
)
from ..student_monitoring.adapters.mongo_repository import (
    MongoDetectionEventRepository,
    MongoVideoSegmentRepository,
)
from ..student_monitoring.ports import DetectionEventRepository, VideoSegmentRepository
from ..student_monitoring.service import StudentMonitoringService
from ..video_monitoring.adapters.memory_repository import MemoryVideoStreamRepository
from ..video_monitoring.adapters.mongo_repository import MongoVideoStreamRepository
from ..video_monitoring.ports import VideoStreamRepository
from ..video_monitoring.service import VideoDemoService, VideoStreamService
from .broadcaster import InMemoryBroadcaster
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
def _detection_event_repository() -> MemoryDetectionEventRepository:
    return MemoryDetectionEventRepository()


@lru_cache
def _video_segment_repository() -> MemoryVideoSegmentRepository:
    return MemoryVideoSegmentRepository()


@lru_cache
def _video_stream_repository() -> MemoryVideoStreamRepository:
    return MemoryVideoStreamRepository()


@lru_cache
def _broadcaster() -> InMemoryBroadcaster:
    return InMemoryBroadcaster()


@lru_cache
def _mongo_client() -> MongoClient[MongoDocument]:
    settings = get_settings()
    if settings.database_url is None:
        raise DatabaseOperationError("DATABASE_URL is required.")
    return create_mongo_client(
        settings.database_url,
        timeout_seconds=settings.database_connect_timeout_seconds,
    )


@lru_cache
def _mongo_database() -> MongoDatabase:
    settings = get_settings()
    if settings.database_name is None:
        raise DatabaseOperationError("DATABASE_NAME is required.")
    return select_database(_mongo_client(), settings.database_name)


@lru_cache
def _mongo_classroom_repository() -> MongoClassroomRepository:
    return MongoClassroomRepository(_mongo_database())


@lru_cache
def _mongo_detection_event_repository() -> MongoDetectionEventRepository:
    return MongoDetectionEventRepository(_mongo_database())


@lru_cache
def _mongo_video_segment_repository() -> MongoVideoSegmentRepository:
    return MongoVideoSegmentRepository(_mongo_database())


@lru_cache
def _mongo_video_stream_repository() -> MongoVideoStreamRepository:
    return MongoVideoStreamRepository(_mongo_database())


def get_classroom_repository(
    settings: Settings = Depends(get_settings),
) -> ClassroomRepository:
    if settings.database_mode == "memory":
        return _classroom_repository()
    return _mongo_classroom_repository()


def get_detection_event_repository(
    settings: Settings = Depends(get_settings),
) -> DetectionEventRepository:
    if settings.database_mode == "memory":
        return _detection_event_repository()
    return _mongo_detection_event_repository()


def get_video_segment_repository(
    settings: Settings = Depends(get_settings),
) -> VideoSegmentRepository:
    if settings.database_mode == "memory":
        return _video_segment_repository()
    return _mongo_video_segment_repository()


def get_video_stream_repository(
    settings: Settings = Depends(get_settings),
) -> VideoStreamRepository:
    if settings.database_mode == "memory":
        return _video_stream_repository()
    return _mongo_video_stream_repository()


def get_broadcaster() -> InMemoryBroadcaster:
    return _broadcaster()


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


def get_video_stream_service(
    repository: VideoStreamRepository = Depends(get_video_stream_repository),
    settings: Settings = Depends(get_settings),
) -> VideoStreamService:
    return VideoStreamService(
        repository=repository,
        stale_seconds=settings.detection_event_stale_seconds,
        clock=utc_now,
    )


def get_student_monitoring_service(
    detection_repository: DetectionEventRepository = Depends(get_detection_event_repository),
    segment_repository: VideoSegmentRepository = Depends(get_video_segment_repository),
    stream_repository: VideoStreamRepository = Depends(get_video_stream_repository),
    broadcaster: InMemoryBroadcaster = Depends(get_broadcaster),
) -> StudentMonitoringService:
    return StudentMonitoringService(
        detection_repository=detection_repository,
        segment_repository=segment_repository,
        stream_repository=stream_repository,
        broadcaster=broadcaster,
    )


def initialize_data_store() -> None:
    settings = get_settings()
    if settings.database_mode == "mongodb":
        database = _mongo_database()
        ping_database(database)
        initialize_indexes(
            database,
            [
                MongoClassroomRepository.ensure_indexes,
                MongoDetectionEventRepository.ensure_indexes,
                MongoVideoSegmentRepository.ensure_indexes,
                MongoVideoStreamRepository.ensure_indexes,
            ],
        )
        return
    if settings.demo_mode_enabled:
        seed_demo_data(
            get_classroom_service(get_classroom_repository(settings), settings), now=utc_now()
        )
        seed_video_streams(_video_stream_repository(), now=utc_now())


def close_data_store() -> None:
    if _mongo_client.cache_info().currsize:
        _mongo_client().close()
    _mongo_classroom_repository.cache_clear()
    _mongo_detection_event_repository.cache_clear()
    _mongo_video_segment_repository.cache_clear()
    _mongo_video_stream_repository.cache_clear()
    _mongo_database.cache_clear()
    _mongo_client.cache_clear()


def verify_readiness(settings: Settings = Depends(get_settings)) -> None:
    if settings.database_mode == "memory":
        return
    try:
        ping_database(_mongo_database())
    except DatabaseOperationError:
        raise DatabaseUnavailableError() from None
